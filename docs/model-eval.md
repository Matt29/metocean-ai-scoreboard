# Évaluation des modèles de post-traitement

Généré par `pipeline/scripts/train.py` le 2026-08-03 08:20 UTC (test = les 30 derniers jours d'émission).

Le modèle **post-traite** la prévision physique officielle (MFWAM pour les
vagues, harmonique pour le niveau d'eau) : il la corrige, il ne la remplace
jamais.

## Résultats par station

| Station | Type | Rows train / test | MAE baseline | MAE baseline débiaisée | MAE modèle | Gain | Verdict |
|---|---|---|---|---|---|---|---|
| pierres-noires | wave | 15286 / 1402 | 0.260 | 0.128 | 0.110 | +57.8% | PASS |
| belle-ile | wave | 15810 / 1402 | 0.146 | 0.102 | 0.090 | +38.5% | PASS |
| anglet | wave | 10768 / 1402 | 0.104 | 0.106 | 0.105 | -0.5% | FAIL |
| cherbourg | wave | 14244 / 1368 | 0.128 | 0.116 | 0.111 | +13.2% | PASS |
| brest | tide | 7338 / 1404 | 0.460 | 0.207 | 0.235 | +48.9% | PASS |
| saint-malo | tide | 7338 / 1404 | 0.455 | 0.400 | 0.320 | +29.5% | PASS |

MAE en m (Hs) pour les stations `wave`, en m (water level) pour les
stations `tide`. « MAE baseline débiaisée » = MAE de la baseline après retrait
de son seul biais moyen sur la fenêtre de test — c'est le garde-fou de la
réserve 3 : un modèle qui ne bat pas cette colonne n'apporte rien de plus
qu'une constante. Gate de mise en ligne : **+5 % de MAE gagnée** sur la
baseline. Une station FAIL reste entraînée et son artefact reste versionné,
mais elle ne doit pas être publiée telle quelle sur le scoreboard.

**Stations sous le gate : anglet** — à ne pas mettre en ligne en l'état.

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
2. **Le gate de +5 % s'applique quand même**, mais il se lit
   « +5 % mesuré sur analyse », pas « +5 % en opérationnel ».
3. **Une large part du gain, sur TOUTES les stations, n'est qu'une correction
   de biais constant.** Chaque baseline dérive sur la fenêtre de test (pour
   les stations `tide`, parce que l'harmonique a été fittée sur les 50 % les
   plus anciens de l'historique ; pour les stations `wave`, biais MFWAM sur la
   période). Biais mesuré (obs − baseline) par station :

   * `pierres-noires` : biais -0.247 m
   * `belle-ile` : biais -0.128 m
   * `anglet` : biais +0.011 m
   * `cherbourg` : biais -0.072 m
   * `brest` : biais -0.453 m
   * `saint-malo` : biais -0.270 m

   La colonne « MAE baseline débiaisée » du tableau isole ce qui reste une
   fois cette constante retirée : c'est elle, et non la MAE baseline brute,
   qui mesure le vrai apport du modèle.
   Stations où le modèle **ne bat pas** ce simple débiaisage : `brest` — leur gain affiché est essentiellement une constante, à ne pas présenter comme du skill météo-océanique.
4. **`anglet` a un historique court** (obs Candhis à partir du 2025-11-18,
   panne de bouée avant) : ~30 % de train en moins que les autres stations
   vagues, et un test plus bruité. C'est la station qui échoue au gate.
   Pistes avant de la publier : plus d'historique, ou une feature de vent
   ARPEGE. Décision de re-spécification à prendre hors Task 7.
