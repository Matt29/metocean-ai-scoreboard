# Produit « pics » — alerte de dépassement et borne haute p90

Date : 2026-09-08. Base : `8e7230b`. Statut : spec validée en discussion, à relire avant plan.

## 1. Pourquoi

Le modèle médian publié minimise une erreur quadratique : il estime la moyenne
conditionnelle, qui se replie vers le centre quand le forçage est incertain,
donc en tempête. Mesuré le 2026-09-08 sur `8e7230b`, folds scellés du protocole
en vigueur (`docs/superpowers/specs/2026-09-08-peaks/event_diag.py`, résultats
dans `event_diag_2026-09-08_8e7230b.json`) : sur le décile supérieur de la
prévision baseline, l'IA n'est pas biaisée au pic (|biais| ≤ 0,3 unité partout)
et bat la baseline sur 8 stations sur 9, mais elle ne restitue que **60 à 80 %
de l'amplitude de la correction observée** sur houle et vent (anglet : 51 %,
seule station où l'IA perd sur les pics, −7 %, une seule origine exploitable).
En production (55 jours au 2026-08-28, même commit), la surcote prédite à brest
et saint-malo n'a que 40 à 45 % de l'écart-type de la surcote observée.

Le gate à +5 % de MAE hors biais reste tel quel : pondérer le médian sur les
pics le ferait échouer. Le produit « pics » est donc **un second et un troisième
output**, à côté du médian, chacun avec son protocole et son propre verdict.

Ce que ce produit remplace : la lecture de `data/extremes.json`, qui sélectionne
les 3 jours au maximum observé et place donc mécaniquement toute prévision sous
l'observation (régression vers la moyenne). Ce fichier reste publié pour le
site, avec cette réserve ajoutée à sa docstring dans `publish.py` ; il ne sert
pas d'évaluation.

## 2. Ce qui est livré

Deux outputs par station, mêmes features que le médian (`feature_columns` de
l'artefact), même assemblage, mêmes origines rolling, même purge, même
bootstrap par jour d'émission.

### 2.1 Alerte de dépassement

- **Cible** par ligne (émission, lead) : `y_evt = obs ≥ seuil`. Pour `tide`,
  `obs` est la **surcote** `obs − harmonique`, jamais le niveau (le décile
  supérieur du niveau est la pleine mer, pas un événement).
- **Deux seuils par station**, calculés sur le **train de chaque fold** :
  `p90` (cible entraînée et gatée) et `p98` (diagnostic seulement, jamais
  gaté). Les seuils diffèrent donc d'un fold à l'autre ; le seuil inscrit dans
  `gate.json` et servi en production est celui du refit de production, calculé
  sur tout l'historique. Un seuil absolu lisible par un opérateur (Hs 2,5 m,
  vent 15 m/s, surcote 30 cm) est reporté en diagnostic dans le rapport, pas
  dans le contrat.
- **Modèle** : `HistGradientBoostingClassifier` (scikit-learn 1.9.0, verrouillé
  dans `uv.lock`), mêmes hyperparamètres que `_hgb()` sauf `class_weight=None`
  et `early_stopping=True`. Un seul candidat, pas de sélection : la classe
  positive fait ~10 % des lignes, un plancher logistique n'apporterait pas de
  décision utile et doublerait le coût.
- **Sorties servies** : `p_h` par heure de l'horizon, plus l'agrégat par
  émission `p_48h = max_h p_h` et `t_peak_pred = argmax_h p_h`. `p_48h` est un
  output dérivé, **évalué comme tel** contre la cible « au moins un dépassement
  dans les 48 h », n = jours d'émission. Aucun produit d'indépendance.
- **Adversaire** : la baseline physique seuillée **à son propre p90 de train**
  (`baseline ≥ p90_baseline`), pas au seuil des obs — une baseline biaisée
  serait sinon un homme de paille, exactement ce que la MAE débiaisée évite
  côté médian. Référence probabiliste : la climatologie (fréquence de la classe
  positive sur le train du fold).
- **Métriques**, sur les lignes de test poolées, IC95 bootstrap 2 000
  réplications par jour d'émission :
  - `bss_clim` : Brier skill score contre la climatologie ;
  - `pod`, `far` à décision 0,5, et les mêmes pour l'adversaire ;
  - `reliability` : courbe en 10 bins, publiée dans le rapport seulement.
- **Gate** : `evaluation_ready AND bss_clim > 0 AND bss_clim_ci95_low > 0 AND
  pod ≥ pod_adversaire`. La dernière clause dit qu'un modèle qui détecte moins
  d'événements que la baseline seuillée ne se publie pas, quel que soit son
  Brier.

### 2.2 Borne haute p90

- **Cible** : la même que le médian (Hs, vitesse, résidu de marée), régressée
  avec `HistGradientBoostingRegressor(loss="quantile", quantile=0.9)`. Pour
  `tide`, quantile du **résidu**, harmonique rajoutée ensuite.
- **Sortie servie** : `p90_h` par heure, publié comme `max(median_h, p90_h)`.
  Le nombre de croisements (`p90 < median`) est compté et reporté en
  diagnostic ; au-delà de 5 % des lignes, la station passe `weak` sur ce
  output.
- **Adversaire** : la baseline décalée de son propre quantile 0,9 d'erreur
  calculé sur le train du fold (`baseline + q90(obs − baseline)`), même
  logique de quantile apparié que ci-dessus.
- **Métriques** : `coverage` (part des obs ≤ borne), `pinball_090` du modèle et
  de l'adversaire, `gain_pinball = (pin_adv − pin_model)/pin_adv`, IC95 par
  jour d'émission.
- **Gate** : `evaluation_ready AND 0.85 ≤ coverage ≤ 0.95 AND
  gain_pinball_ci95_low > 0`.

### 2.3 Indépendance des verdicts

Chaque output a son propre `pass`. Une station peut publier son alerte sans
que son médian passe, et inversement ; le site décide de l'affichage. Les
quatre stations `wave` sont en protocole dégradé (`evaluation_ready = false`)
tant que l'historique n'atteint pas 730 jours : leurs outputs « pics » sont
mesurés et reportés, jamais publiés, comme le médian.

## 3. Où ça vit

### 3.1 Entraînement (`pipeline/scripts/train.py`, `src/scoreboard/model.py`)

- `model.py` : deux constructeurs, `_hgb_classifier()` et `_hgb_quantile()`,
  et un artefact par station `<station>-peaks.joblib` :
  `{classifier, quantile, feature_columns, thresholds: {p90, p98}, kind}`.
  Staging et promotion passent par `stage`/`promote_transaction` existants, dans
  la **même transaction** que le médian et le gate.
- `train.evaluate` : pour chaque origine retenue, après le fit du candidat
  médian, fit du classifieur et du quantile sur le même `x_train` ; prédictions
  empilées avec celles du médian sur le même `x_test`. Les deux outputs
  n'influencent jamais la sélection du candidat médian.
- Nouvelles fonctions pures, testables sans données : `_peak_scores(...)`
  (Brier, POD, FAR, coverage, pinball), `_peak_thresholds(train_obs)`,
  `_peak_confidence_intervals(...)` réutilisant le bootstrap par jour
  d'émission existant.
- `merge_gate` : ajouter la sous-entrée `peaks` à la liste de clés qu'il
  reconstruit — **il repart d'une liste fixe**, un sous-champ non listé serait
  perdu au prochain retrain ciblé. Forme :

```json
"peaks": {
  "alert": {"pass", "weak", "threshold_p90", "threshold_p98", "bss_clim",
            "bss_clim_ci95_low", "bss_clim_ci95_high", "pod", "far",
            "pod_baseline", "far_baseline", "n_events"},
  "band":  {"pass", "weak", "coverage", "pinball_model", "pinball_baseline",
            "gain_pinball", "gain_pinball_ci95_low", "gain_pinball_ci95_high",
            "crossings_frac"}
}
```

- `daily.validate_gate` et `load_gate` ne vérifient que `pass`/`weak` au
  niveau station : inchangés. Une entrée `peaks` absente vaut « non publié ».
- Rapport généré `docs/model-eval.md` : nouvelle section « Pics — alerte et
  borne haute », avec les seuils, les métriques, la courbe de fiabilité en
  tableau, et la mention « ce verdict ne modifie pas le gate médian ». Le
  tableau événements existant est corrigé au passage : la bande absolue
  « |résidu| > 30 cm » devient propre au `kind` (30 cm tide, 0,5 m wave,
  2 m/s wind) — sur le vent elle sélectionnait 14 000 lignes sur 17 000.

### 3.2 Service (`src/scoreboard/daily.py`, `publish.py`)

- `daily.issue_series` charge aussi `<station>-peaks.joblib` si le gate
  `peaks` publie au moins un output, prédit sur les **mêmes features** que le
  médian, et passe les colonnes à `publish`.
- Nouveau fichier par station, additif, `schema_version: 1` :

```
data/<id>/peaks.json  {"station","issued","threshold_p90","unit",
                       "p_48h","t_peak_pred",
                       "series":[{"t","p_exceed","p90_upper"}]}
```

  `p_exceed` et `p90_upper` valent `null` pour un output non publié. Les
  contrats `latest.json`, `history.json`, `scores.json` ne bougent pas.
- Scores publics : nouveau fichier `data/peaks_scores.json`, séparé de
  `scores.json` (même règle que `extremes.json`), avec par station et par
  fenêtre 30 j / 90 j : `n_events`, `pod`, `far`, `bss_clim`, `coverage`,
  calculés par `daily` sur les jours rescorés, en pondérant par `n_points`
  comme `compute_scores`. La cible réelle en production est le seuil de
  `gate.json`, appliqué aux obs rescorées.
- `stations.json` : deux booléens additifs `peaks_alert_published`,
  `peaks_band_published`.

### 3.3 Docs (même commit que le code, règle CLAUDE.md)

`docs/plan-dev-modele.md` (section « Pics » avec le tableau du diagnostic
2026-09-08 daté et commité), `docs/demandes-produit.md` (le produit, les deux
seuils, l'adversaire), `docs/data-sources.md` (rien, pas de nouvelle source),
`README.md` (nouveaux fichiers du contrat), `publish.py` docstring
(`peaks.json`, `peaks_scores.json`, réserve sur `extremes.json`),
`docs/dev-dashboard.html` s'il liste les artefacts.

## 4. Erreurs et cas limites

- Fold sans événement positif en test (`n_events = 0`) : métriques d'alerte à
  `None`, fold compté mais non gatable ; au poolé, `n_events < 24` heures rend
  l'alerte `evaluation_ready = false` pour cette station.
- Classifieur qui ne converge pas ou classe constante : `pass = false`,
  l'artefact médian est promu quand même — l'échec d'un output pics ne bloque
  pas la release du médian, mais il est loggé et présent dans le rapport.
- Artefact `-peaks.joblib` manquant en service pour une station dont le gate
  publie un output pics : erreur explicite, station `missing` sur `peaks.json`
  seulement, `latest.json` publié normalement.
- Features identiques au médian : aucun nouveau train/serve skew possible par
  construction, `feature_columns` de l'artefact pics doit être égal à celui du
  médian, vérifié à la promotion.

## 5. Tests

- Unitaires purs : `_peak_thresholds`, `_peak_scores` (cas connus : classifieur
  parfait → BSS 1, climatologie → 0 ; borne = obs → coverage 1 ; pinball d'un
  quantile constant), fusion `merge_gate` avec et sans `peaks`, écriture
  `peaks.json` avec `null`.
- Intégration existante `train.py` sur fixture : le rapport contient la
  nouvelle section, le gate contient `peaks`, la transaction promeut trois
  fichiers par station.
- `daily` sur fixture : `peaks.json` écrit seulement si un output est publié,
  `latest.json` byte-identique à avant.
- Suite complète + `ruff` + `git diff --check` avant commit, `/simplify` avant
  commit (règle CLAUDE.md).

## 6. Coût et réserves

- Deux fits HGB de plus par origine et par station : temps de protocole ×3
  environ (mesuré médian seul le 2026-08-05 : 20 à 220 s par station). Le
  retrain complet reste sous 30 min ; à surveiller, `review_codex_2026-08-05`
  § 6 le signale déjà.
- Un seul candidat par output : pas de plancher linéaire. Si un jour la
  question « le boosting est-il payé » se pose pour l'alerte, la réponse passe
  par un `compare_ridge.py` étendu, pas par ce lot.
- Les seuils p90/p98 par station ne sont pas des seuils opérationnels
  universels : un utilisateur qui veut « Hs > 2,5 m » lit la valeur physique
  du seuil dans `peaks.json` et le diagnostic absolu du rapport.

## 7. Hors périmètre

Rendu sur le site ODC (lot séparé, repo séparé). Modèles de valeurs extrêmes
par station (trop peu d'événements sur 2 ans). Toute nouvelle feature.
Modification du gate médian.
