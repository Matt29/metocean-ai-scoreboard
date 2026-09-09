# Évaluation des modèles de post-traitement

Généré par `pipeline/scripts/train.py` le 2026-09-09 05:20 UTC (fenêtres temporelles et protocole détaillés par station ci-dessous).

Le modèle **post-traite** une prévision physique officielle : il la corrige, il
ne la remplace jamais. Cette baseline n'est plus imposée : pour une station
`wave`, c'est le **meilleur modèle physique** parmi les 5 modèles de vagues
Open-Meteo, et pour une station `wind` le meilleur des 3 modèles de vent
Open-Meteo — dans les deux cas choisi station par station comme le plus proche
de son observation **sur les seuls jours d'émission d'entraînement** (colonne
« Baseline »). Pour une station `tide`, c'est la prédiction harmonique.

## Résultats par station

| Station | Type | Baseline production / folds de test | Modèle ML | Rows train / test | MAE baseline | MAE baseline débiaisée | MAE modèle | Gain affiché | **Gain hors biais** | IC95% gain | Protocole | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| brest | tide | harmonique / harmonique | `hgb-per-lead` | 42084 / 17484 | 0.118 | 0.117 | 0.054 | +54.2% | **+53.9%** | [+50.8%, +56.7%] (365 jours) | holdout annuel (1×365j) | PASS |
| saint-malo | tide | harmonique / harmonique | `hgb-per-lead` | 43596 / 17484 | 0.151 | 0.151 | 0.099 | +34.2% | **+34.1%** | [+31.6%, +36.4%] (365 jours) | holdout annuel (1×365j) | PASS |
| ouessant | wind | meteofrance_arpege_europe / meteofrance_arpege_europe, meteofrance_arpege_europe, meteofrance_arpege_europe, meteofrance_arpege_europe | `hgb` | 43772 / 17238 | 1.238 | 1.227 | 0.969 | +21.7% | **+21.0%** | [+19.2%, +22.7%] (360 jours) | rolling-origin multi-saisons (4×90j) | PASS |
| dieppe | wind | meteofrance_arpege_europe / meteofrance_arpege_europe, meteofrance_arpege_europe, meteofrance_arpege_europe, meteofrance_arpege_europe | `hgb` | 43768 / 17236 | 1.294 | 1.231 | 0.808 | +37.5% | **+34.3%** | [+31.9%, +36.5%] (360 jours) | rolling-origin multi-saisons (4×90j) | PASS |
| cherbourg-vent | wind | meteofrance_arpege_europe / meteofrance_arpege_europe, meteofrance_arpege_europe, meteofrance_arpege_europe, meteofrance_arpege_europe | `hgb` | 43736 / 17232 | 1.635 | 1.428 | 1.086 | +33.6% | **+24.0%** | [+21.2%, +26.6%] (360 jours) | rolling-origin multi-saisons (4×90j) | PASS |

**Stations non ré-entraînées sur cette fenêtre : pierres-noires, belle-ile, anglet, cherbourg** — leur
jeu d'entraînement est absent de `pipeline/data_train/`. Leur artefact et leur
entrée `gate.json` du run précédent sont **conservés tels quels** : ils ne sont
ni supprimés ni rafraîchis, et les chiffres ci-dessus ne les couvrent pas.

MAE en m (water level) pour les stations `tide`, m/s (vent 10 m) pour les stations `wind`. « MAE baseline débiaisée » = MAE de la baseline après retrait
de son biais moyen dans chaque fold de test — c'est le garde-fou de la
réserve 4 : un modèle qui ne bat pas cette colonne n'apporte rien de plus
qu'une constante. Gate de mise en ligne : **+5 % de MAE gagnée hors biais**
(critère 3 de la spec) — le gate porte sur `gain_debiased`, jamais sur le
gain affiché, précisément pour qu'une station ne passe pas sur un simple
débiaisage. Une station FAIL reste entraînée et son artefact reste
versionné, mais elle ne doit pas être publiée telle quelle sur le
scoreboard. Le gain est aussi assorti d'un **IC95 % bootstrap par jour
d'émission** : les leads d'un même run ne sont jamais traités comme des
observations indépendantes. Le gate exige en plus une borne basse strictement
positive ; il ne transforme donc pas un gain ponctuel incertain en PASS.
Une station `wave` ou `wind` en `holdout dégradé` reste FAIL jusqu'à ce que
les quatre folds saisonniers soient suffisamment couverts.

**`PASS*`** = la station passe le gate mais **ne bat pas sa propre baseline
débiaisée** : son gain affiché est essentiellement une constante, pas du skill.
Ne pas mettre ce chiffre en avant sans la réserve 4. Le gate portant désormais
sur le gain hors biais, une station `PASS` ne peut plus être `weak` — `PASS*`
ne peut donc plus apparaître pour une station retrainée ici ; le mécanisme est
conservé tel quel pour compatibilité avec `gate.json`.

Ce verdict est aussi émis en donnée dans `pipeline/models/gate.json`
(`{station: {pass, weak, mae_model, mae_baseline, gain, gain_debiased,
gain_debiased_ci95_low, gain_debiased_ci95_high, n_folds, n_issue_days,
evaluation_protocol, evaluation_ready, ci_unit, baseline_model,
fold_baselines}}`) — c'est cette
source, pas ce tableau, que le publisher doit lire.

**Stations sous le gate dans `gate.json` : cherbourg** — à ne pas mettre en ligne en l'état.

## Skill sur les événements — diagnostic, pas un critère

La MAE sur la fenêtre entière est dominée par les heures calmes, où la
baseline physique est déjà quasi optimale et où tous les candidats font
match nul. Elle répond à « le modèle est-il meilleur un jour ordinaire ? »,
que personne ne demande. Le tableau ci-dessous restreint la mesure aux
heures où il y a quelque chose à prévoir.

**Ce tableau ne décide rien.** Le gate reste sur la fenêtre entière :
restreindre la métrique aux heures où un modèle réussit le mieux serait
exactement le déplacement de poteaux que ce projet refuse. Il est là pour
dire *où* le skill se trouve, pas pour repêcher une station.

Le débiaisage utilise le biais de la **fenêtre entière**, jamais un biais
recalculé sur la bande : une correction par tempête n'est pas quelque chose
que la baseline pourrait connaître à l'avance.

| Station | Bande | Heures | MAE baseline | MAE baseline débiaisée | MAE modèle | Gain hors biais |
|---|---|---|---|---|---|---|
| brest | décile sup. | 1750 | 0.325 | 0.326 | 0.092 | **+72.0%** |
| brest | |résidu| > 0.3 m | 814 | 0.389 | 0.393 | 0.098 | **+75.1%** |
| saint-malo | décile sup. | 1750 | 0.411 | 0.411 | 0.165 | **+59.8%** |
| saint-malo | |résidu| > 0.3 m | 2032 | 0.397 | 0.397 | 0.160 | **+59.7%** |
| ouessant | décile sup. | 1728 | 3.559 | 3.563 | 2.224 | **+37.6%** |
| ouessant | |résidu| > 2 m/s | 3302 | 2.941 | 2.942 | 1.831 | **+37.7%** |
| dieppe | décile sup. | 1727 | 3.450 | 3.451 | 1.743 | **+49.5%** |
| dieppe | |résidu| > 2 m/s | 3771 | 2.845 | 2.845 | 1.316 | **+53.8%** |
| cherbourg-vent | décile sup. | 1726 | 4.728 | 3.709 | 1.724 | **+53.5%** |
| cherbourg-vent | |résidu| > 2 m/s | 5250 | 3.357 | 2.499 | 1.525 | **+39.0%** |

## Pics — alerte de dépassement et borne haute

Deux outputs entraînés sur les mêmes lignes de train et scorés sur les mêmes
folds scellés que le modèle médian. Leur verdict **ne modifie pas le gate** du
médian : chacun a le sien (`gate.json` → `peaks.alert.pass`, `peaks.band.pass`).
Pour une station `tide`, la cible est la surcote `obs − harmonique`.

Alerte : probabilité que la cible dépasse le p90 de son train. Adversaire :
la baseline physique seuillée à son propre p90 de train (absent pour `tide`,
l'harmonique n'a pas de surcote). Référence du Brier : la climatologie du train.

`p_48h` (le maximum de `p_h` sur l'horizon) est évalué à part, une ligne par
jour d'émission, contre « au moins un dépassement dans les 48 h » : `p48 BSS`
et `n jours`. **Diagnostic, jamais gaté** — sa référence est la fréquence des
jours à événement mesurée sur le test lui-même, donc un skill optimiste.

| Station | Seuil p90 | Seuil p98 | BSS clim. | IC95 % | POD / FAR modèle | POD / FAR baseline | Événements | p48 BSS | n jours | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| brest | 0.202 | 0.414 | +0.509 | [+0.395 ; +0.608] | 0.76 / 0.31 | — / — | 1428 | +0.730 | 365 | PASS |
| saint-malo | 0.255 | 0.461 | +0.231 | [+0.163 ; +0.295] | 0.47 / 0.47 | — / — | 1448 | +0.303 | 365 | PASS |
| ouessant | 12.600 | 16.958 | +0.671 | [+0.634 ; +0.704] | 0.76 / 0.16 | 0.73 / 0.27 | 2372 | +0.719 | 360 | PASS |
| dieppe | 8.000 | 11.100 | +0.606 | [+0.563 ; +0.647] | 0.70 / 0.18 | 0.57 / 0.48 | 2090 | +0.637 | 360 | PASS |
| cherbourg-vent | 10.900 | 14.400 | +0.598 | [+0.549 ; +0.643] | 0.75 / 0.23 | 0.65 / 0.42 | 2148 | +0.622 | 360 | PASS |

Borne haute : quantile 0,9 de la cible. Adversaire : la baseline décalée de son
propre quantile 0,9 d'erreur de train. `max(médian, p90)` est publié ; la part de
croisements est un diagnostic (`weak` au-delà de 5 %).

| Station | Couverture | Pinball modèle | Pinball baseline | Gain pinball | IC95 % | Croisements | Verdict |
|---|---|---|---|---|---|---|---|
| brest | 0.882 | 0.0115 | 0.0307 | +62.7% | [+59.2% ; +66.0%] | 2.6% | PASS |
| saint-malo | 0.862 | 0.0233 | 0.0356 | +34.7% | [+31.4% ; +38.0%] | 1.6% | PASS |
| ouessant | 0.849 | 0.2448 | 0.3105 | +21.1% | [+18.3% ; +24.2%] | 0.1% | FAIL |
| dieppe | 0.840 | 0.2080 | 0.3035 | +31.5% | [+28.8% ; +34.0%] | 0.1% | FAIL |
| cherbourg-vent | 0.880 | 0.2492 | 0.3629 | +31.3% | [+28.4% ; +34.4%] | 0.5% | PASS |

## Comparaison des modèles ML

Gain **hors biais sur la dernière fenêtre de VALIDATION** — pas sur le test.
Chaque origine rolling répète cette sélection dans son seul passé, puis son
gagnant touche le test suivant. Le tableau montre la validation la plus
récente, celle qui choisit aussi l'artefact de production ; le score agrégé
n'est jamais utilisé pour choisir entre les candidats. Les valeurs ci-dessous
ne sont donc pas comparables au test — fenêtre différente, modèle entraîné
sur moins de données.

`ridge` est le **plancher honnête** : un gradient boosting qui ne le bat pas
ne paie pas sa complexité, et c'est un résultat, pas un échec.

| Station | Baseline physique | `hgb` | `ridge` | `hgb-per-lead` | Publié |
|---|---|---|---|---|---|
| brest | tide | +40.8% | +41.0% | **+42.8%** | `hgb-per-lead` |
| saint-malo | tide | +25.5% | +10.7% | **+28.4%** | `hgb-per-lead` |
| ouessant | meteofrance_arpege_europe | **+21.5%** | +10.2% | +20.9% | `hgb` |
| dieppe | meteofrance_arpege_europe | **+32.4%** | +28.0% | +31.8% | `hgb` |
| cherbourg-vent | meteofrance_arpege_europe | **+25.3%** | +15.2% | +23.8% | `hgb` |

## Protocole

* **Split temporel par jour d'émission.** Une ligne du dataset est un couple
  (émission 06 UTC, lead 1–48 h) ; les lignes d'une même émission partagent
  `last_err` / `mean_err_24h`. Découper sur le temps de validité ferait donc
  fuir une émission entre train et test. Le jour d'émission est reconstruit
  comme `valid_time - lead_h`. Une émission entière reste toujours du même
  côté d'une frontière. Jamais de split aléatoire.
* **Choix de la baseline (stations `wave`).** Les 5 modèles de vagues
  Open-Meteo sont comparés à la bouée **sur les seuls jours d'émission
  d'entraînement**, et le plus proche devient la baseline de la station — donc
  le dénominateur de tous les gains ci-dessus. La sélection ne voit jamais la
  fenêtre de test : sinon la baseline serait choisie par les données mêmes qui
  servent à la juger, ce qui gonflerait mécaniquement le gain. Elle peut donc
  différer entre folds : le rapport et `gate.json` publient cette liste
  séparément de la baseline re-sélectionnée sur tout l'historique pour la
  production.
* **Choix du modèle ML — sur validation, jamais sur le test.** Les
  120 derniers jours d'émission **du train** forment une fenêtre de
  validation. Les trois candidats (`hgb`, `ridge`, `hgb-per-lead`) y sont
  comparés, à features et baseline identiques ; le meilleur gain hors biais
  gagne, est ré-entraîné sur tout le train, puis évalué **une seule fois** sur
  le test. Choisir le modèle sur le test publierait un maximum sur trois
  tirages faits sur la même fenêtre — la même fuite que la sélection de
  baseline évite, un étage plus haut.
* **Stations `wave` / `wind` — rolling-origin multi-saisons quand l'archive le
  permet.** À partir de 730 jours d'émissions observées, quatre origines
  chronologiques sont espacées d'au moins 90 jours et évaluent chacune 90
  jours par défaut. Un `--test-days` plus long élargit aussi l'espacement,
  afin que les tests ne se chevauchent jamais. Chaque origine
  re-sélectionne baseline et modèle uniquement sur son passé, avec une purge
  de 48 h avant le test. Les fenêtres sont non chevauchantes. Avec moins de
  730 jours, le rapport dit `holdout dégradé`, ne prétend pas couvrir
  plusieurs saisons et ne permet pas un PASS. Chaque fold doit aussi couvrir
  au moins 80% de ses jours attendus.
* **Incertitude par station.** L'IC95 % du gain hors biais est un bootstrap
  déterministe de jours d'émission entiers : les 48 leads corrélés d'un run
  ne deviennent jamais 48 pseudo-réplications. Le biais de la baseline est
  ré-estimé dans chaque réplication et chaque fold. Le gate exige à la fois
  un gain ponctuel d'au moins 5% et une borne basse strictement positive.
* **Cible.** Stations `wave` : l'observation Hs. Stations `tide` : le résidu
  `obs - harmonique` ; le niveau publié est réassemblé en
  `harmonique + résidu prédit`, et c'est sur ce niveau reconstitué que la MAE
  ci-dessus est calculée — sinon les chiffres ne seraient pas comparables
  entre stations.
* Tous les horodatages sont en UTC.

## Réserves importantes sur l'interprétation

1. **Le skill des stations `wave` est un plafond mesuré sur passé reconstitué,
   pas sur prévision réelle.** Faute d'archive libre des runs de vagues passés,
   la baseline d'entraînement vient de la fenêtre historique de l'API Open-Meteo
   Marine, qui n'est pas le run à +1–48 h qu'aura la production. Le couple
   (baseline, obs) vu à l'entraînement n'est donc pas celui que verra la
   production : ces gains sont un **plafond**, pas une estimation du skill
   opérationnel, et la direction de l'écart n'est pas déterminable a priori. Le
   ré-entraînement sur de vraies prévisions archivées interviendra après ~1 mois
   de runs quotidiens ; ces chiffres seront alors remplacés.
2. **Pour les stations `tide`, le forçage est une prévision ECMWF passée,
   stratifiée par âge de run.** L'entraînement et le service emploient le même
   modèle `ecmwf_ifs025` via l'API Previous Runs : pour chaque émission, les
   features vent 10 m **et pression** viennent du run qui était réellement
   disponible à cette date et à ce lead. Ce choix supprime le skew antérieur
   ERA5/ARPEGE et le faux avantage d'un vent connu après coup. Il reste une
   limite d'archive : ce forçage n'est disponible qu'à partir du 2024-02-05,
   ce qui borne l'historique `tide` utilisable. **La granularité des Previous
   Runs reste journalière** : aux leads courts, le run le plus frais du jour
   peut être postérieur à l'émission de 06 UTC. Le replay est donc plus proche
   de l'opérationnel que ERA5, mais pas une reconstruction causalement exacte
   à l'heure près.
3. **Le gate de +5 % s'applique quand même** au replay stratifié par âge
   de run, avec la limite de granularité journalière explicitée ci-dessus :
   ce n'est plus une analyse parfaite a posteriori, sans être une causalité
   exacte à l'heure près.
4. **Sur 0 des 5 stations ré-entraînées, plus de la
   moitié du gain
   affiché n'est qu'une correction de biais constant** — chaque baseline dérive
   sur la fenêtre de test, et retirer ce seul offset capte déjà l'essentiel du
   gain. Le chiffre à citer est donc **« Gain hors biais »**, jamais « Gain
   affiché ». Détail par station (biais obs − baseline, puis les deux gains) :

   * `brest` : biais -0.009 m — gain affiché +54.2%, **hors biais +53.9%**
   * `saint-malo` : biais -0.003 m — gain affiché +34.2%, **hors biais +34.1%**
   * `ouessant` : biais -0.033 m — gain affiché +21.7%, **hors biais +21.0%**
   * `dieppe` : biais -0.013 m — gain affiché +37.5%, **hors biais +34.3%**
   * `cherbourg-vent` : biais +1.090 m — gain affiché +33.6%, **hors biais +24.0%**

   Aucune station ré-entraînée n'a un gain affiché supérieur au double de son gain hors biais.
   Aucune station de `gate.json` n'est `weak` : toutes battent ce simple débiaisage.
5. **Aucune station ré-entraînée n'est sous le gate sur cette fenêtre de
   test.**

   Hors de ce run, `gate.json` garde sous le gate : cherbourg —
   station(s) non ré-entraînée(s) ici, verdict inchangé.


## Pistes testées et écartées

* **Pression au niveau de la mer** (`pressure_msl` Open-Meteo, servie dans la
  même requête que le vent, ajoutée comme anomalie à 1013,25 hPa). Motivation :
  le baromètre inverse (~1 cm de niveau par hPa) est le premier moteur de la
  surcote, donc du résidu à prédire sur les stations `tide`. **Mesurée le
  2026-08-03 par ablation à fenêtre identique, elle dégrade 5 stations sur 6 et
  a été retirée.** Δ de gain hors biais dus à la seule pression :

  | station | kind | Δ pression |
  |---|---|---|
  | pierres-noires | wave | −2,0 pts |
  | belle-ile | wave | −1,0 pt |
  | anglet | wave | −2,4 pts |
  | cherbourg | wave | −5,1 pts |
  | brest | tide | −2,0 pts |
  | saint-malo | tide | **+4,8 pts** (mais reste sous le gate) |

  Seule `saint-malo` en profitait, `anglet` tombait sous le gate à cause
  d'elle. Lecture d'alors : sur un historique court, une colonne sans effet
  direct sur les stations `wave` ajoute surtout de la variance.

  **Verdict rouvert le 2026-08-04, et inversé pour les `tide` seulement.**
  Les deux mesures `tide` ci-dessus comparaient à la baseline harmonique de
  90 jours, dont la constituante annuelle non résolue laissait une dérive
  saisonnière dans le résidu. Pression et dérive sont toutes deux basse
  fréquence : l'ablation ne pouvait pas les séparer, et le verdict a donc été
  pris dans le seul régime où il était ininterprétable. Re-mesurée sur la
  baseline à 730 jours, la pression rapporte **+17 points** sur `brest`.
  Elle est servie aux `tide` via `wind.TIDE_FORCING_COLUMNS`, et à elles
  seules — une houle n'a pas de réponse baromètre inverse. L'objection
  « deux chemins de features » ne tenait pas : `features.py` porte déjà
  `WAVE_FEATURE_COLUMNS` et `WIND_FEATURE_COLUMNS`, seule la *fonction*
  `build_features` est unique, et elle le reste.
  Détail : `.superpowers/sdd/2026-07-30-scoreboard-metocean-ia/task-7C-report.md`.
