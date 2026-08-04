# Plan de dev — modélisation

Backlog des pistes de modélisation, ouvert le 2026-08-04. Complète
`docs/demandes-produit.md`, qui porte les demandes **produit** ; ce fichier-ci
porte ce qui touche aux features, à la baseline et à l'entraînement.

Règle qui vaut pour toute cette liste : **une piste n'est acquise que mesurée
par ablation à fenêtre et split identiques** (`train.py --ablate <colonnes>`).
Un gain plausible n'est pas un gain. Trois itérations ont déjà été dépensées à
retirer des gains qui n'en étaient pas.

Et un ordre de priorité qui n'est pas négociable : **améliorer la baseline
physique passe avant d'ajouter de la capacité au modèle**. Le 2026-08-04 l'a
montré deux fois — une baseline harmonique portée de 90 j à 2 ans a fait plus
bouger les chiffres que n'importe quel changement d'algorithme, et dans le sens
de la vérité.

---

## Fait le 2026-08-04

- **Baseline harmonique 90 j → 730 j glissants** (`harmonic.FIT_LOOKBACK_DAYS`).
  Motif : à 90 j, utide ne sépare ni S2/K2 ni K1/P1 (182,6 j requis) et ne porte
  pas Sa (365 j). Corollaire découvert au passage : le backtest ajustait sur un
  historique **croissant** (182 → 365 j) quand la production ajustait sur 90 j —
  un train/serve skew sur la baseline elle-même, jamais détecté parce qu'une
  docstring affirmait l'équivalence sans que personne ne la vérifie.
- **Plancher `MIN_TIDE_FIT_DAYS` aligné sur la fenêtre cible** des deux côtés.
  Une nouvelle station `tide` reste `missing` tant qu'elle n'a pas deux ans d'obs.
- **Pression MSL réintroduite, sur les stations `tide` uniquement**
  (`wind.TIDE_FORCING_COLUMNS`). Elle avait été retirée le 2026-08-03 après
  mesure — mais cette mesure comparait à la baseline 90 j dérivante, et une
  dérive saisonnière comme un signal de pression sont tous deux basse fréquence,
  donc inséparables dans ce régime. Re-mesurée : **+17 points de gain hors biais
  sur brest**. Le chemin `wave`/`wind` reste sans pression (elle y coûtait 1 à
  5 points, et une hauteur de houle n'a pas de réponse baromètre inverse).
- **Fenêtre harmonique portée à 730 j** après mesure causale (voir plus bas).
- **Conséquence publiée** : **brest et saint-malo passent toutes deux**
  (`gate.json` du 2026-08-04 : gain hors biais +53,25 % et +30,16 %,
  `weak: false`) — pour la première fois sur le critère dur, et non sur une
  entrée héritée d'une règle périmée. **8 stations publiées**, cherbourg (houle)
  seule sous le gate. Ces gains `tide` sont à lire avec la réserve de forçage
  quasi-analyse ci-dessous : ils ne sont pas des gains opérationnels.

  Étapes intermédiaires gardées pour mémoire, parce qu'elles montrent à quel
  point la profondeur de fit décide du verdict : à 365 j, brest passait à
  +7,97 % et saint-malo échouait à +4,95 % (0,05 point sous le seuil) sur une
  fenêtre de test d'un mois ; à 90 j, les deux étaient sous le gate.

  **La leçon du jour, contre-intuitive et à ne pas oublier** : améliorer la
  baseline physique n'a pas réduit la marge du modèle, ça a supprimé
  l'*avantage injuste du concurrent*. Tant que la baseline dérivait de ±20 cm
  par mois, la « baseline débiaisée » du gate pouvait retirer gratuitement
  7,8 cm de biais sur le mois de test : un adversaire artificiellement fort,
  contre lequel le modèle faisait match nul (−0,2 %). Baseline conditionnée,
  ce biais tombe à 0,4 cm et le skill réel apparaît (+8,0 %). Corollaire
  méthodologique : **un modèle qui échoue contre une baseline biaisée n'a pas
  été mesuré, il a été comparé à un artefact.**

---

## Fenêtre harmonique 730 j — ✅ fait le 2026-08-04

Un fit **non causal** sur 2 ans laisse un résidu à 13,3 cm d'écart-type, contre
**21,6 cm** pour le fit causal glissant à 365 j en place. 365 j est exactement
le seuil de Rayleigh pour Sa, donc la constituante annuelle y est estimée au
bord de sa séparabilité : estimation bruitée, extrapolation médiocre à chaque
refit.

**Tranché le 2026-08-04 par comparaison causale**, 3 ans d'obs, même fenêtre
d'évaluation (365 derniers jours), refit tous les 30 j, seule la profondeur du
fit change :

| station | fenêtre | MAE résidu | écart-type | biais mensuel pic-à-pic |
|---|---|---|---|---|
| brest | 365 j | 16,82 cm | 20,77 cm | 46,3 cm |
| brest | **730 j** | **11,87 cm** | **15,57 cm** | **31,7 cm** |
| saint-malo | 365 j | 17,44 cm | 22,21 cm | 38,0 cm |
| saint-malo | **730 j** | **15,55 cm** | **19,89 cm** | **28,8 cm** |

**−29 % de MAE sur brest, −11 % sur saint-malo**, en causal, sans fuite. Le
plafond non causal de 13,3 cm était donc surtout de la profondeur de fenêtre et
non de la fuite : le causal à 730 j (15,6 cm d'écart-type) en approche de près.

Appliqué : `FIT_LOOKBACK_DAYS = 730`, `--days` par défaut à 1825 (2 ans pour
ajuster, 3 pour évaluer), les deux stations ré-entraînées. Le coût quotidien
passe à ~160 Mo de REFMAR par run — c'est ce qui a rouvert le cache des
coefficients (ci-dessous), écarté deux fois auparavant à juste titre.

**Aucune nouvelle feature ne se mesure contre une baseline en cours de
changement.** Une ablation contre une baseline dont on sait qu'elle va
s'améliorer de 29 % ne mesure rien de durable — c'est exactement ce qui a rendu
caduque la mesure de la pression du 2026-08-03. Règle générale, pas anecdote.

---

## Protocole d'évaluation — ✅ P1/P2/P3 faits le 2026-08-04

Trois défauts découverts le 2026-08-04, en tirant sur la question « la surcote
est-elle prévisible ? ». Ils passent **avant** toute nouvelle feature : ils
déterminent ce qu'une mesure de feature voudra dire.

### P1. La fenêtre de test était saisonnière, donc le verdict aussi — ✅ corrigé

`--test-days 30` prend les 30 derniers jours d'émission — donc le mois où l'on
ré-entraîne. Sur la façade Atlantique-Manche, la surcote est un phénomène
d'hiver. Un retrain en juillet évalue au creux annuel, un retrain en novembre au
pic : **les deux verdicts ne sont pas comparables**. Le PASS de brest et le FAIL
à 0,05 point de saint-malo sont tous deux, en partie, des notes de saison.

Corrigé : `train.TEST_DAYS_BY_KIND = {"tide": 365}` — une année pleine de test,
toutes saisons, split temporel contigu préservé. **Limitation laissée en clair**
plutôt que masquée : la fenêtre de *validation* reste plafonnée à
`VAL_DAYS_CAP = 120` jours (sinon elle mange le train), donc la **sélection** du
modèle reste saisonnièrement biaisée même si le **verdict** ne l'est plus.
`wave` et `wind` gardent 30 jours : le même argument vaut pour eux (Hs est très
saisonnier), c'est du travail ouvert, pas un choix tranché.

### P2. La métrique était dominée par les heures sans rien à prévoir — ✅ corrigé

Une MAE sur toutes les heures moyenne majoritairement du temps calme, où
harmonique seule est déjà quasi optimale et où tout le monde fait match nul.
Opérationnellement, ce qui compte est l'événement : la tempête qui met 40 cm
au-dessus de la marée prédite.

À ajouter comme **diagnostic**, pas comme gate : MAE sur le décile supérieur de
|résidu|, et MAE au-delà d'un seuil (30 cm). Un modèle qui gagne sur les
événements en faisant match nul sur le calme est un bon produit et une
affirmation vraie ; un pourcentage global ne le dit pas.

**Le gate n'a pas été remplacé par cette métrique.** Il reste dur et inchangé —
déplacer les poteaux après le tir est précisément ce que ce projet reproche aux
autres. Le conditionnel s'ajoute à côté (`train._event_diagnostic`, section
« Skill sur les événements » de `model-eval.md`, avec la mention explicite
« ce tableau ne décide rien »).

Piège évité et testé : le débiaisage des bandes utilise le biais de la **fenêtre
entière**, jamais recalculé sur la bande — sinon on offrirait à la baseline une
correction par tempête qu'elle ne peut pas connaître à l'avance, soit un homme
de paille dans l'autre sens.

### P3. Saisonnalité de la surcote — ✅ mesurée le 2026-08-04

MAE du résidu par mois, depuis `data_train/<station>.parquet` (baseline 730 j) :

| | juillet | moyenne toutes saisons | pic |
|---|---|---|---|
| brest | **7,1 cm** | 10,9 cm | **21,2 cm** (févr. 26) |
| saint-malo | **11,8 cm** | 14,8 cm | 20,0 cm (janv. 26) |

**Brest porte 3× plus de surcote en février qu'en juillet.** C'est ce chiffre
qui a motivé P1 : la fenêtre de test d'alors était juillet, et le PASS de brest
à +7,97 % avait donc été obtenu dans le mois où il y avait le *moins* à prévoir.

Ce que ça a confirmé : P1 et P2 n'étaient pas des raffinements. Un facteur 3 entre
le mois de test et le pic annuel signifie que le verdict d'une station `tide`
dépend aujourd'hui autant du calendrier que du modèle.

---

## ⚠️ Les gains `tide` mesurés le 2026-08-04 ne sont pas des gains opérationnels

**À lire avant d'utiliser le moindre chiffre de `model-eval.md` sur brest ou
saint-malo** (+53 % et +30 % hors biais, 2/2 PASS).

Le forçage d'entraînement provient de l'API Historical Forecast d'Open-Meteo,
qui **concatène les runs les plus frais** : à chaque heure valide elle sert une
prévision émise quelques heures plus tôt, jamais 24 ou 48 h avant. Mesuré le
2026-08-04 contre ERA5 sur 1440 h à Brest : **corrélation 0,9997** sur la
pression, 0,25 hPa d'écart moyen pour un signal d'écart-type 13 hPa. C'est de
l'analyse, pas de la prévision. (Le passage d'ERA5 à cette API le même jour n'a
donc rien changé aux chiffres : +53,4 % → +53,1 %. Il a supprimé une
incohérence — les trois kinds partagent enfin une source — pas l'optimisme.)

**Pourquoi ça mord plus fort sur la marée que sur la houle.** `mae_base` est
plate en fonction du lead (0,120 à 0,124 de 1 h à 48 h) : la baseline
astronomique ne se dégrade pas avec l'échéance, c'est un calcul d'éphémérides.
Sur une station de houle, la baseline est elle-même une prévision physique qui
se dégrade *en même temps* que le forçage, et une part de l'erreur est partagée.
Sur la marée, non : **toute la dégradation du forçage tombe du seul côté du
modèle**. Le gain `tide` est structurellement plus optimiste que le gain `wave`,
par propriété de la variable et non par défaut de code.

Signature à surveiller : le gain ne décroît que faiblement avec le lead (brest
63 % à 0-6 h, 50 % à 36-48 h). Avec un vrai forçage prévu, la pente serait bien
plus raide — le peu de décroissance observé vient de `last_err` / `mean_err_24h`
qui vieillissent, pas du forçage.

### Piste retenue, à instruire : forcer la marée à l'ECMWF

Rien n'oblige un modèle de surcote à être nourri à l'ARPEGE. La **Previous Runs
API** d'Open-Meteo sert `ecmwf_ifs025` avec des **leads stratifiés 1 à 7 jours**
— de vraies prévisions à échéance, ce qu'ARPEGE n'a pas sur cette API. Entraîner
*et* servir les stations `tide` sur ECMWF rendrait la mesure honnête
immédiatement, sans attendre l'accumulation de `data_forecast_archive/`.

À vérifier avant de s'engager : que `pressure_msl` y soit servie, la profondeur
d'archive réelle, et que le chemin de service (`fetch_wind_forecast`) puisse
servir le même modèle — sinon on remplace un skew par un autre.

L'autre chemin reste `pipeline/data_forecast_archive/` (démarré le 2026-08-03,
un Parquet par jour) : le seul instrument réellement vrai, mais qui demande des
mois.

---

## Constantes harmoniques persistées — conception retenue le 2026-08-04

Aujourd'hui le run quotidien re-télécharge **2 ans** de REFMAR
(`TIDE_FIT_LOOKBACK_DAYS`, ~50 chunks, ~160 Mo) et relance `utide.solve` pour
reconstruire chaque matin une analyse qui décrit un *site*, pas une journée. Le
métier ne fait pas ça : le SHOM publie des constantes, les ports s'en servent
des années.

Conception : ajuster une fois, **persister les coefficients**
(`HarmonicModel.save`/`.load` existent déjà dans `harmonic.py` et ne sont
utilisés nulle part en production), servir la baseline depuis l'artefact, et
ré-ajuster sur une cadence. Le fetch quotidien retombe alors aux
`OBS_LOOKBACK_DAYS` = 4 jours dont le run a besoin par ailleurs pour `last_err`
et le scoring de la veille — deux ordres de grandeur de moins.

Trois conditions :

1. **La cadence de refit doit être la même à l'entraînement et en production.**
   Non négociable, et c'est la seule qui peut casser le projet : si la
   production rafraîchit tous les 6 mois pendant que `causal_predict` rafraîchit
   tous les 30 jours, le backtest note une baseline plus fraîche que celle
   servie. C'est le skew éliminé le matin même, réintroduit par la porte de
   derrière. `refit_days` devient une constante partagée, comme
   `FIT_LOOKBACK_DAYS`.
2. **Ne pas extrapoler la tendance séculaire** (`utide.solve(trend=False)`).
   C'est la cicatrice du module : un fit figé avait porté un offset de −0,3 m.
   Cette observation datait d'un fit sur 90 jours, où la tendance est du bruit —
   sur 730 jours elle est bien conditionnée — mais l'extrapoler reste le risque
   inutile de la conception.
3. **Une date de fit dans l'artefact**, et un run qui refuse de servir au-delà
   d'une péremption. Sans ça, un cron cassé sert silencieusement un fit vieux
   de deux ans.

**Mesuré le 2026-08-04, cadence retenue : 180 jours.** `causal_predict` sur la
même fenêtre d'évaluation (2025-07-12 → 2026-08-04, 9313 h, `trend=False`), MAE
de la baseline harmonique seule :

| refit | Brest | Saint-Malo |
|---|---|---|
| 30 j | 11,65 cm | 14,99 cm |
| 180 j | 11,72 cm | 15,63 cm |
| 365 j | 11,85 cm | 16,11 cm |

Six mois de péremption coûtent **0,7 mm à Brest et 6,4 mm à Saint-Malo** — sous
le centimètre, donc le cas « quelques millimètres → prendre 6 mois ». La cadence
annuelle, elle, se paie (+1,1 cm à Saint-Malo) : 180 est le palier avant le
décrochage, pas un maximum arbitraire.

Implémenté : `harmonic.REFIT_DAYS` (constante partagée backtest/production),
`HarmonicModel.fitted_at` persisté, `scripts/fit_harmonic.py` pour ré-ajuster,
et `daily` qui marque la station `missing` au-delà de la péremption. Le fetch
REFMAR quotidien passe de ~50 requêtes / ~160 Mo à une seule requête de 4 jours,
et le `daily --dry-run` complet tombe de ~50 s à 2,4 s.

---

## Features à tester, par valeur physique attendue

### 1. Tension de vent — la prochaine

Aujourd'hui le modèle reçoit `wind_u10` / `wind_v10`, donc une **vitesse**. La
surcote répond à la **tension** exercée par le vent sur la surface, qui varie en
U², pas en U. Un vent de 20 m/s ne pousse pas deux fois plus d'eau qu'un vent de
10 m/s, il en pousse quatre fois plus. Donner U à un modèle en attendant qu'il
retrouve U² lui fait dépenser de la capacité sur une transformation connue
d'avance.

Forme retenue — deux colonnes, aucune configuration par station :

```
wind_stress_u = wind_u10 * hypot(wind_u10, wind_v10)
wind_stress_v = wind_v10 * hypot(wind_u10, wind_v10)
```

C'est τ à un facteur constant près (ρ_air · C_d). Garder les composantes u/v
plutôt qu'un `U²·cos(θ−θ₀)` avec un axe par station : l'axe qui empile l'eau
dépend de la géométrie locale, et un modèle par station le retrouve seul à
partir de deux composantes. Introduire un `θ₀` en config, ce serait un
paramètre à régler à la main par station, donc une porte ouverte au
sur-ajustement silencieux.

À mesurer : `--ablate wind_stress_u,wind_stress_v`. Question ouverte à trancher
sur les chiffres — garder u/v **et** la tension (le modèle arbitre), ou
remplacer. Garder les deux double les colonnes de forçage ; à n'accepter que si
l'ablation montre que les deux paires portent chacune de l'information.

### 2. Tendance de pression (dP/dt)

La surcote ne répond pas seulement à la pression locale mais au déplacement du
système dépressionnaire. `pressure_anom.diff()` sur 3 h et 6 h est la forme la
plus simple ; c'est aussi un proxy du champ de vent au large, que la station
ne voit pas.

Attention : une différence temporelle sur une série de prévision se comporte
différemment d'une différence sur une réanalyse. À vérifier explicitement,
c'est exactement la famille de skew que ce projet paie régulièrement.

### 3. Interaction marée-surcote

Déterminante à Saint-Malo (macrotidal, marnage jusqu'à ~13 m). Une surcote ne
s'additionne pas à la marée : elle dépend de la hauteur d'eau, donc de la phase.
La dérivée temporelle de la prédiction harmonique — le courant de marée, en
première approximation — capte cette phase sans ajouter de source de données.

C'est la piste la plus prometteuse pour saint-malo spécifiquement, et elle
coûte une colonne dérivée d'une donnée déjà en main.

### 4. Mémoire plus longue du résidu

Le modèle a `last_err` et `mean_err_24h`. La surcote est autocorrélée sur 6 à
12 h ; une moyenne à 24 h lisse précisément l'échelle utile. Ajouter
`mean_err_3h` / `mean_err_6h`.

Le moins cher de la liste : aucune donnée nouvelle, aucune requête, une
fonction déjà écrite. À faire passer en premier si le temps manque.

---

## Réserves ouvertes

- **Divergence validation/test sur saint-malo.** Avec la pression, sa validation
  double (+4,2 % → +8,8 % hors biais) mais son test s'effondre (−0,3 % →
  −11,7 %). Brest a le même mois de test et y gagne : ce n'est donc pas
  saisonnier, c'est spécifique à la station. **Non expliqué.** À reprendre avant
  de conclure quoi que ce soit sur saint-malo.
- **Dette brest « régression sous la feature vent ».** Reformulée, pas résolue :
  les ablations qui l'avaient mesurée l'ont été contre la baseline 90 j, dont le
  biais mensuel balayait ±20 cm. Elles mesuraient du bruit autour d'un biais.
  À rejouer entièrement sur la baseline actuelle avant d'y voir une anomalie.
- **Skew ERA5 restant dans `backfill.py`.** Le chemin d'entraînement `tide` est
  passé aux prévisions ARPEGE archivées le 2026-08-04, mais `backfill.py`
  reconstruit encore ses journées avec `fetch_wind_history` (ERA5). C'est
  assumé — un jour rejoué est marqué `backfilled` et n'est jamais présenté
  comme une note en direct — mais ça reste une incohérence à traiter le jour où
  l'historique rejoué sert à autre chose qu'à remplir le graphe.

---

## Références verticales — ce que le RAM donne et ne donne pas

`docs/RAM_PACK/` (Références Altimétriques Maritimes, SHOM). Fichier
`CSV/RAM.csv`, **tab-délimité malgré son extension**. Ligne Brest :
`NM = 4,14 m` au-dessus du zéro hydrographique, `ZH_Ref = −3,635`,
`REFERENCE = IGN69`, `DATE_RF = 2010`.

**Ce n'est pas un levier de modélisation.** Le pipeline ne lit aucune référence
verticale : `harmonic.fit` estime sa propre constante à partir des
observations, donc un décalage de datum ne peut pas atteindre le modèle. Le
niveau moyen observé sur 2025-2026 est de 4,29 m, soit 15 cm au-dessus du NM du
RAM — époques de référence différentes, aucune conséquence sur le résidu.

Utile comme **contrôle de cohérence** : il confirme que les niveaux REFMAR
servis sont bien sur le zéro hydrographique et pas sur un autre plan. À
ressortir si une nouvelle station de marée entre au scoreboard, pour vérifier
son plan de référence avant de l'entraîner.
