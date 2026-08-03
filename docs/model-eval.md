# Évaluation des modèles de post-traitement

Généré par `pipeline/scripts/train.py` le 2026-08-03 09:26 UTC (test = les 30 derniers jours d'émission).

Le modèle **post-traite** la prévision physique officielle (MFWAM pour les
vagues, harmonique pour le niveau d'eau) : il la corrige, il ne la remplace
jamais.

## Résultats par station

| Station | Type | Rows train / test | MAE baseline | MAE baseline débiaisée | MAE modèle | Gain affiché | **Gain hors biais** | Verdict |
|---|---|---|---|---|---|---|---|---|
| pierres-noires | wave | 15286 / 1402 | 0.260 | 0.121 | 0.089 | +65.8% | **+26.4%** | PASS |
| belle-ile | wave | 15810 / 1402 | 0.154 | 0.101 | 0.077 | +50.1% | **+24.5%** | PASS |
| anglet | wave | 10960 / 1402 | 0.102 | 0.105 | 0.096 | +5.7% | **+8.4%** | PASS |
| cherbourg | wave | 14246 / 1368 | 0.110 | 0.102 | 0.107 | +2.4% | **-5.2%** | FAIL |
| brest | tide | 7243 / 1404 | 0.087 | 0.064 | 0.067 | +22.9% | **-5.4%** | PASS\* |
| saint-malo | tide | 7243 / 1404 | 0.117 | 0.116 | 0.132 | -13.3% | **-14.1%** | FAIL |

MAE en m (Hs) pour les stations `wave`, en m (water level) pour les
stations `tide`. « MAE baseline débiaisée » = MAE de la baseline après retrait
de son seul biais moyen sur la fenêtre de test — c'est le garde-fou de la
réserve 4 : un modèle qui ne bat pas cette colonne n'apporte rien de plus
qu'une constante. Gate de mise en ligne : **+5 % de MAE gagnée** sur la
baseline. Une station FAIL reste entraînée et son artefact reste versionné,
mais elle ne doit pas être publiée telle quelle sur le scoreboard.

**`PASS*`** = la station passe le gate mais **ne bat pas sa propre baseline
débiaisée** : son gain affiché est essentiellement une constante, pas du skill.
Ne pas mettre ce chiffre en avant sans la réserve 4.

Ce verdict est aussi émis en donnée dans `pipeline/models/gate.json`
(`{station: {pass, weak, mae_model, mae_baseline, gain, gain_debiased}}`) —
c'est cette
source, pas ce tableau, que le publisher doit lire.

**Stations sous le gate : cherbourg, saint-malo** — à ne pas mettre en ligne en l'état.

## Protocole

* **Split temporel par jour d'émission.** Une ligne du dataset est un couple
  (émission 06 UTC, lead 1–48 h) ; les lignes d'une même émission partagent
  `last_err` / `mean_err_24h`. Découper sur le temps de validité ferait donc
  fuir une émission entre train et test. Le jour d'émission est reconstruit
  comme `valid_time - lead_h`, et les 30 derniers jours d'émission
  forment le test. Jamais de split aléatoire.
* **Cible.** Stations `wave` : l'observation Hs. Stations `tide` : le résidu
  `obs - harmonique` ; le niveau publié est réassemblé en
  `harmonique + résidu prédit`, et c'est sur ce niveau reconstitué que la MAE
  ci-dessus est calculée — sinon les chiffres ne seraient pas comparables
  entre stations.
* Tous les horodatages sont en UTC.

## Réserves importantes sur l'interprétation

1. **Le skill des stations `wave` est un plafond mesuré sur analyse, pas sur
   prévision réelle.** Faute d'archive libre des runs MFWAM passés, la
   baseline d'entraînement est l'**analyse** MFWAM, qui assimile les bouées
   Candhis — donc les observations mêmes qui servent de vérité terrain. Le
   couple (baseline, obs) vu à l'entraînement n'est donc pas celui que verra
   la production : ces gains sont un **plafond mesuré sur analyse**, pas une
   estimation du skill opérationnel, et la direction de l'écart n'est pas
   déterminable a priori. Le ré-entraînement sur de vraies prévisions
   archivées interviendra après ~1 mois de runs quotidiens ; ces chiffres
   seront alors remplacés.
2. **Le vent d'entraînement est un vent parfait, celui de production ne le
   sera pas.** La feature vent est apprise sur la **réanalyse ERA5** (0,25°,
   ECMWF, vent connu après coup) et sera servie avec une **prévision ARPEGE
   Europe** (0,1°, Météo-France), qui porte une erreur de lead time que la
   réanalyse n'a pas. Ce n'est **pas** une équivalence : deux familles de
   modèles, deux grilles, et une partie du gain ci-dessous ne survivra pas au
   passage en opérationnel. Même catégorie de compromis que la réserve 1, et
   même issue : il se résorbera quand le run quotidien aura accumulé assez de
   ses propres prévisions pour ré-entraîner dessus. Détail dans
   `docs/data-sources.md` §4bis.
3. **Le gate de +5 % s'applique quand même**, mais il se lit
   « +5 % mesuré sur analyse, avec un vent parfait », pas « +5 % en
   opérationnel ».
4. **Sur 4 des 6 stations, plus de la moitié du gain
   affiché n'est qu'une correction de biais constant** — chaque baseline dérive
   sur la fenêtre de test, et retirer ce seul offset capte déjà l'essentiel du
   gain. Le chiffre à citer est donc **« Gain hors biais »**, jamais « Gain
   affiché ». Détail par station (biais obs − baseline, puis les deux gains) :

   * `pierres-noires` : biais -0.253 m — gain affiché +65.8%, **hors biais +26.4%**
   * `belle-ile` : biais -0.134 m — gain affiché +50.1%, **hors biais +24.5%**
   * `anglet` : biais +0.019 m — gain affiché +5.7%, **hors biais +8.4%**
   * `cherbourg` : biais -0.050 m — gain affiché +2.4%, **hors biais -5.2%**
   * `brest` : biais -0.072 m — gain affiché +22.9%, **hors biais -5.4%**
   * `saint-malo` : biais -0.012 m — gain affiché -13.3%, **hors biais -14.1%**

   Stations dont le gain affiché vaut **au moins le double** de son gain hors biais : `pierres-noires`, `belle-ile`, `cherbourg`, `brest` — leur chiffre de tête est d'abord du débiaisage.
   Stations où le modèle **ne bat pas** ce simple débiaisage : `cherbourg`, `brest`, `saint-malo` — il n'y apporte rien de plus qu'une constante, à ne pas présenter comme du skill météo-océanique.
5. **Stations sous le gate — à ne pas publier en l'état.** Le modèle n'y
   atteint pas les +5% exigés : il ne trouve pas de signal exploitable
   dans les features actuelles. Le forçage vent 10 m (`wind_u10`/`wind_v10`)
   fait désormais partie de ces features — son ajout en Task 7B a payé sur les
   stations de houle exposée mais **pas** sur les stations ci-dessous, donc
   l'explication est ailleurs : historique d'entraînement trop court, forçage
   local mal représenté par la maille du modèle de vent, ou grandeur encore
   absente (pression au niveau de la mer pour la surcote). À trancher station
   par station, mesure à l'appui — `train.py --ablate-wind` chiffre ce que le
   vent apporte réellement à chacune.

   * `cherbourg` (wave) : 14246 lignes de train, MAE baseline 0.110 → modèle 0.107 (+2.4% affiché, -5.2% hors biais)
   * `saint-malo` (tide) : 7243 lignes de train, MAE baseline 0.117 → modèle 0.132 (-13.3% affiché, -14.1% hors biais)
