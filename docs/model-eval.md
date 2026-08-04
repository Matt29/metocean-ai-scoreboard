# Évaluation des modèles de post-traitement

Généré par `pipeline/scripts/train.py` le 2026-08-04 12:59 UTC (test = les 365 derniers jours d'émission).

Le modèle **post-traite** une prévision physique officielle : il la corrige, il
ne la remplace jamais. Cette baseline n'est plus imposée : pour une station
`wave`, c'est le **meilleur modèle physique** parmi les 5 modèles de vagues
Open-Meteo, et pour une station `wind` le meilleur des 3 modèles de vent
Open-Meteo — dans les deux cas choisi station par station comme le plus proche
de son observation **sur les seuls jours d'émission d'entraînement** (colonne
« Baseline »). Pour une station `tide`, c'est la prédiction harmonique.

## Résultats par station

| Station | Type | Baseline (meilleur modèle physique) | Modèle ML | Rows train / test | MAE baseline | MAE baseline débiaisée | MAE modèle | Gain affiché | **Gain hors biais** | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| brest | tide | harmonique | `hgb-per-lead` | 33254 / 17484 | 0.121 | 0.122 | 0.057 | +53.1% | **+53.2%** | PASS |
| saint-malo | tide | harmonique | `hgb-per-lead` | 34794 / 17484 | 0.157 | 0.157 | 0.110 | +30.0% | **+30.2%** | PASS |

**Stations non ré-entraînées sur cette fenêtre : pierres-noires, belle-ile, anglet, cherbourg, ouessant, dieppe, cherbourg-vent** — leur
jeu d'entraînement est absent de `pipeline/data_train/`. Leur artefact et leur
entrée `gate.json` du run précédent sont **conservés tels quels** : ils ne sont
ni supprimés ni rafraîchis, et les chiffres ci-dessus ne les couvrent pas.

MAE en m (water level) pour les stations `tide`. « MAE baseline débiaisée » = MAE de la baseline après retrait
de son seul biais moyen sur la fenêtre de test — c'est le garde-fou de la
réserve 4 : un modèle qui ne bat pas cette colonne n'apporte rien de plus
qu'une constante. Gate de mise en ligne : **+5 % de MAE gagnée hors biais**
(critère 3 de la spec) — le gate porte sur `gain_debiased`, jamais sur le
gain affiché, précisément pour qu'une station ne passe pas sur un simple
débiaisage. Une station FAIL reste entraînée et son artefact reste
versionné, mais elle ne doit pas être publiée telle quelle sur le
scoreboard.

**`PASS*`** = la station passe le gate mais **ne bat pas sa propre baseline
débiaisée** : son gain affiché est essentiellement une constante, pas du skill.
Ne pas mettre ce chiffre en avant sans la réserve 4. Le gate portant désormais
sur le gain hors biais, une station `PASS` ne peut plus être `weak` — `PASS*`
ne peut donc plus apparaître pour une station retrainée ici ; le mécanisme est
conservé tel quel pour compatibilité avec `gate.json`.

Ce verdict est aussi émis en donnée dans `pipeline/models/gate.json`
(`{station: {pass, weak, mae_model, mae_baseline, gain, gain_debiased,
baseline_model}}`) — c'est cette
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
| brest | décile sup. | 1750 | 0.339 | 0.334 | 0.075 | **+77.5%** |
| brest | |résidu| > 30 cm | 938 | 0.394 | 0.388 | 0.076 | **+80.4%** |
| saint-malo | décile sup. | 1750 | 0.425 | 0.422 | 0.201 | **+52.3%** |
| saint-malo | |résidu| > 30 cm | 2274 | 0.399 | 0.396 | 0.190 | **+52.1%** |

## Comparaison des modèles ML

Gain **hors biais sur la fenêtre de VALIDATION** — pas sur le test. Les trois
candidats sont entraînés sur le train privé de sa validation, comparés sur
cette validation, et seul le gagnant (en gras) est ré-entraîné sur tout le
train puis évalué **une seule fois** sur le test : les chiffres du tableau
« Résultats par station » ne sont donc jamais un maximum sur trois tirages.
Les valeurs ci-dessous ne sont pas comparables à celles du test — fenêtre
différente, modèle entraîné sur moins de données.

`ridge` est le **plancher honnête** : un gradient boosting qui ne le bat pas
ne paie pas sa complexité, et c'est un résultat, pas un échec.

| Station | Baseline physique | `hgb` | `ridge` | `hgb-per-lead` | Publié |
|---|---|---|---|---|---|
| brest | tide | +11.8% | +8.3% | **+13.8%** | `hgb-per-lead` |
| saint-malo | tide | +4.9% | -1.1% | **+6.1%** | `hgb-per-lead` |

## Protocole

* **Split temporel par jour d'émission.** Une ligne du dataset est un couple
  (émission 06 UTC, lead 1–48 h) ; les lignes d'une même émission partagent
  `last_err` / `mean_err_24h`. Découper sur le temps de validité ferait donc
  fuir une émission entre train et test. Le jour d'émission est reconstruit
  comme `valid_time - lead_h`, et les 365 derniers jours d'émission
  forment le test. Jamais de split aléatoire.
* **Choix de la baseline (stations `wave`).** Les 5 modèles de vagues
  Open-Meteo sont comparés à la bouée **sur les seuls jours d'émission
  d'entraînement**, et le plus proche devient la baseline de la station — donc
  le dénominateur de tous les gains ci-dessus. La sélection ne voit jamais la
  fenêtre de test : sinon la baseline serait choisie par les données mêmes qui
  servent à la juger, ce qui gonflerait mécaniquement le gain.
* **Choix du modèle ML — sur validation, jamais sur le test.** Les
  120 derniers jours d'émission **du train** forment une fenêtre de
  validation. Les trois candidats (`hgb`, `ridge`, `hgb-per-lead`) y sont
  comparés, à features et baseline identiques ; le meilleur gain hors biais
  gagne, est ré-entraîné sur tout le train, puis évalué **une seule fois** sur
  le test. Choisir le modèle sur le test publierait un maximum sur trois
  tirages faits sur la même fenêtre — la même fuite que la sélection de
  baseline évite, un étage plus haut.
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
2. **Pour les stations `tide` uniquement, le forçage atmosphérique
   d'entraînement est parfait, celui de production ne le sera pas.** Le vent
   10 m (seule feature de forçage restante — la pression n'y est plus, voir
   « Pistes testées et écartées » ci-dessous) est appris, pour ces stations,
   sur la **réanalyse ERA5** (0,25°, ECMWF, connue après coup ;
   `scripts/build_dataset.py`, chemin tide) et sera servi avec une **prévision
   ARPEGE Europe** (0,1°, Météo-France), qui porte une erreur de lead time que
   la réanalyse n'a pas. Ce n'est **pas** une équivalence : deux familles de
   modèles, deux grilles, et une partie du gain ci-dessous ne survivra pas au
   passage en opérationnel. Les 4 stations `wave` ré-entraînées ici n'ont pas
   ce skew : leur vent d'entraînement vient déjà des 3 mêmes modèles de
   prévision que le serve (Historical Forecast API, voir
   `docs/data-sources.md` §4bis). Même catégorie de compromis que la réserve 1
   pour les stations `tide`, et même issue : il se résorbera quand le run
   quotidien aura accumulé assez de ses propres prévisions pour ré-entraîner
   dessus. Détail dans `docs/data-sources.md` §4bis.
3. **Le gate de +5 % s'applique quand même**, mais il se lit
   « +5 % mesuré sur analyse, avec un vent parfait », pas « +5 % en
   opérationnel ».
4. **Sur 0 des 2 stations ré-entraînées, plus de la
   moitié du gain
   affiché n'est qu'une correction de biais constant** — chaque baseline dérive
   sur la fenêtre de test, et retirer ce seul offset capte déjà l'essentiel du
   gain. Le chiffre à citer est donc **« Gain hors biais »**, jamais « Gain
   affiché ». Détail par station (biais obs − baseline, puis les deux gains) :

   * `brest` : biais +0.009 m — gain affiché +53.1%, **hors biais +53.2%**
   * `saint-malo` : biais +0.012 m — gain affiché +30.0%, **hors biais +30.2%**

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
