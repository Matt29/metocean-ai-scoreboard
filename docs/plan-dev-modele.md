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
- **Forçage `tide` stratifié par âge de run** (Previous Runs API, `ecmwf_ifs025`,
  entraînement *et* service) : une ligne à +48 h est forcée par une prévision
  vieille de deux jours. Ça lève la réserve de forçage quasi-analyse, mesures à
  l'appui — section dédiée plus bas.
- **Les trois dernières features du backlog, mesurées** — phase de marée et
  tendance de pression gardées, tension de vent jetée. Section dédiée plus bas.
  Publié : brest +50,4 → **+53,9 %**, saint-malo +29,8 → **+34,1 %**. Le backlog
  de features est vide ; ce qui reste ouvert est du diagnostic, pas de la liste.
- **Conséquence publiée** : **brest et saint-malo passent toutes deux**
  (`gate.json` du 2026-08-04 : gain hors biais +53,25 % et +30,16 %,
  `weak: false`) — pour la première fois sur le critère dur, et non sur une
  entrée héritée d'une règle périmée. **8 stations publiées**, cherbourg (houle)
  seule sous le gate. Ces gains ont été **re-mesurés sur un forçage de
  prévision réel le 2026-08-04** (voir la section suivante) : +53,3 % et
  +28,0 %.

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

## Le forçage de prévision, mesuré — la réserve est levée

**Statut : résolue le 2026-08-04.** Cette section remplace une réserve qui
disait l'inverse ; elle est gardée entière parce que le raisonnement qui a
motivé le doute reste juste, et que c'est la mesure, pas l'argument, qui l'a
tranché.

### Le doute

Le forçage d'entraînement provenait de l'API Historical Forecast d'Open-Meteo,
qui **concatène les runs les plus frais** : à chaque heure valide elle sert une
prévision émise quelques heures plus tôt, jamais 24 ou 48 h avant. Mesuré
contre ERA5 sur 1440 h à Brest : **corrélation 0,9997** sur la pression,
0,25 hPa d'écart moyen pour un signal d'écart-type 13 hPa. C'est de l'analyse,
pas de la prévision.

Pourquoi ça devait mordre plus fort sur la marée que sur la houle : `mae_base`
est plate en fonction du lead (0,120 à 0,124 de 1 h à 48 h), la baseline
astronomique ne se dégradant pas avec l'échéance. Sur une station de houle, la
baseline est elle-même une prévision physique qui se dégrade *en même temps*
que le forçage, et une part de l'erreur est partagée. Sur la marée, non : toute
la dégradation du forçage devait tomber du seul côté du modèle.

### Ce qui a été fait

Les stations `tide` sont passées à la **Previous Runs API** d'Open-Meteo et au
modèle `ecmwf_ifs025` (`wind.fetch_tide_forcing_history`). Pour chaque heure
valide, l'API sert le run du jour même, celui de la veille et celui de
l'avant-veille ; `wind.forcing_at_issue` donne à chaque ligne le run que son
émission aurait réellement eu. Une ligne à +48 h est donc forcée par une
prévision vieille de deux jours, et le run quotidien sert le même modèle.

Trois faits d'API à connaître avant d'y toucher (sondés le 2026-08-04) :

- **ARPEGE n'est pas stratifié** sur cette API : 0 % de couverture sur toutes
  les colonnes `*_previous_day*` de vent. Changer d'endpoint ne suffisait pas,
  il fallait changer de modèle — d'où le passage du chemin `tide` à ECMWF
  jusque dans le service.
- **La profondeur d'archive dépend de l'échéance demandée** : `previous_day2`
  démarre au 2024-02-05, `previous_day7` plus tard. C'est `TIDE_FORCING_START`,
  et c'est désormais ce qui borne un dataset de marée — plus les observations.
  Le jeu brest passe de 50 738 à 42 084 lignes, le train de ~2 ans à ~1,5 an,
  la fenêtre de test d'un an étant inchangée.
- **La granularité est journalière, pas par run.** Les échéances sous ~18 h
  gardent donc un reste d'optimisme : elles sont servies par le run le plus
  frais du jour d'émission. Rien dans le code ne peut le corriger ; tout ce qui
  dépasse 24 h, là où le doute portait, est une vraie prévision vieillie.

### Le résultat, et pourquoi il tient

| | forçage quasi-analyse | forçage stratifié |
|---|---|---|
| brest, gain hors biais | +53,1 % | **+53,3 %** |
| saint-malo, gain hors biais | +30,0 % | **+28,0 %** |
| brest, 1-6 h → 37-48 h | 63 % → 50 % | 63,4 % → 50,4 % |

La signature qu'on surveillait — une pente gain/échéance bien plus raide — **ne
s'est pas produite**. La stratification est pourtant bien appliquée : à heure
valide égale, une ligne vue à courte et à longue échéance porte des valeurs
différentes dans 99 % des cas (écart moyen 0,50 m/s et 0,39 hPa).

Le forçage n'est pas décoratif pour autant. Ablation des trois colonnes
(`--ablate wind_u10,wind_v10,pressure_anom`) : brest +53,3 % → **+39,0 %**,
saint-malo +28,0 % → **+18,0 %**. Il achète donc 14 et 10 points — mais son
**âge** n'en coûte quasiment aucun.

L'ordre de grandeur explique les deux faits ensemble. L'erreur ECMWF à +48 h
vaut 1,4 hPa sur la pression et 1,05 m/s sur le vent (Brest, décembre 2025).
1,4 hPa de baromètre inverse, c'est ~1,4 cm d'eau, contre 5,6 cm de MAE modèle
et un résidu qui monte à 40 cm en tempête. La surcote répond à un forçage de
grande échelle et lentement variable, exactement le régime qu'un modèle global
prévoit bien à deux jours.

**Conséquence** : les gains `tide` peuvent être cités sans réserve de forçage.
Ce qui reste vrai, et qui n'a rien à voir : ce sont des chiffres de backtest sur
une année de test tenue à l'écart, pas un historique de production.

L'autre chemin reste `pipeline/data_forecast_archive/` (démarré le 2026-08-03,
un Parquet par jour) : la seule mesure véritablement vraie, à des mois
d'échéance. Il n'est plus le seul instrument disponible.

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
   inutile de la conception. Re-mesuré et **tranché le 2026-08-05** dans le
   régime servi : voir « `trend=False` — réserve rouverte et fermée » plus bas.
3. **Une date de fit dans l'artefact**, et un run qui refuse de servir au-delà
   d'une péremption. Sans ça, un cron cassé sert silencieusement un fit vieux
   de deux ans.

**Mesuré le 2026-08-04, cadence retenue : 30 jours** — après avoir failli
retenir 180, ce qui est la partie instructive.

Première mesure, sur la **baseline seule** (`causal_predict`, même fenêtre
d'évaluation : 2025-07-12 → 2026-08-04, 9313 h, `trend=False`) :

| refit | Brest | Saint-Malo |
|---|---|---|
| 30 j | 11,65 cm | 14,99 cm |
| 180 j | 11,72 cm | 15,63 cm |
| 365 j | 11,85 cm | 16,11 cm |

Six mois de péremption coûtent 0,7 mm à Brest et 6,4 mm à Saint-Malo — sous le
centimètre, donc le cas « quelques millimètres → prendre 6 mois ». Verdict
provisoire : 180 j.

**Re-mesuré de bout en bout, après le modèle ML** — la seule quantité qu'on
publie. Les deux changements du chantier ont dû être séparés, parce qu'ils
avaient été introduits ensemble :

| configuration | brest | saint-malo |
|---|---|---|
| refit 30 j, `trend=True` (état d'avant le chantier) | +53,3 % — 5,6 cm | +31,4 % — 10,6 cm |
| refit 30 j, `trend=False` | **+50,7 % — 5,8 cm** | **+29,9 % — 10,6 cm** |
| refit 180 j, `trend=False` | +48,7 % — 6,1 cm | +31,1 % — 10,8 cm |

À Brest, `trend=False` coûte 2,6 pts et la cadence à 180 j en coûte 2,0 de plus.
Le produit perd donc 3 mm sur la cadence là où la baseline seule n'en perdait que
0,7 : **le modèle ML compense en partie les refits fréquents**, et une mesure
prise sur la baseline seule ne pouvait pas le voir. À Saint-Malo la cadence est
neutre, voire favorable.

30 j rend ces 2 points et ne coûte rien d'opérationnel : le fit de ~50 s tombe
une fois par mois au lieu d'une fois par jour, et le semestre n'aurait économisé
que 50 s de plus par an. **Leçon générale, payée une fois de plus : mesurer la
quantité publiée, jamais un proxy en amont** — c'est la même erreur de forme que
la pression écartée le 2026-08-03 sur une baseline dérivante.

`trend=False` reste retenu malgré ses 2,6 pts : c'est la condition 2, et elle
achète un biais 2 à 3× meilleur (−1,4 / −1,8 cm contre +0,6 / −0,1 cm).

### `trend=False` — réserve rouverte et fermée le 2026-08-05

La réserve était : *cet arbitrage n'a pas été re-mesuré à 30 jours de
péremption, à rouvrir si on veut récupérer ces 2,6 points.* Re-mesuré dans le
régime réellement servi (fit 730 j, refit 30 j, péremption active), de bout en
bout **après le modèle ML**, sur la fenêtre 2024-02-07 → 2026-08-05 (test = 365
derniers jours d'émission, 17 484 lignes, sélection du candidat ML sur la
validation interne au train de chaque bras) :

| station | `trend` | MAE baseline | biais baseline | MAE publiée | gain hors biais | IC95 % |
|---|---|---|---|---|---|---|
| brest | `False` | 11,75 cm | **−0,83 cm** | **5,41 cm** | +53,7 % | [+50,6 %, +56,3 %] |
| brest | `True` | 11,85 cm | +1,04 cm | 5,34 cm | +55,1 % | [+51,7 %, +58,1 %] |
| saint-malo | `False` | 15,05 cm | **−0,22 cm** | **9,99 cm** | +33,6 % | [+31,3 %, +35,9 %] |
| saint-malo | `True` | 15,48 cm | +1,30 cm | 10,01 cm | +35,4 % | [+33,1 %, +37,5 %] |

Bootstrap **apparié** par jour d'émission (2 000 tirages, les deux bras notés
sur le même ré-échantillonnage, comme pour les ablations) :

| station | Δ MAE publiée (`True` − `False`) | Δ gain hors biais |
|---|---|---|
| brest | **−0,74 mm**, IC95 [−2,81 mm, +1,31 mm] | +1,45 pts, IC95 [−0,35, +3,15] |
| saint-malo | **+0,22 mm**, IC95 [−1,31 mm, +1,69 mm] | +1,80 pts, IC95 [+0,64, +2,99] |

**Décision : on garde `trend=False`.** Trois raisons, dans cet ordre.

1. **Sur la quantité publiée, `trend=True` ne rend rien.** −0,7 mm à Brest,
   +0,2 mm à Saint-Malo, les deux IC à cheval sur zéro. Il n'y a pas de 2,6
   points à récupérer sur ce qu'on sert.
2. **Les « points » étaient un effet de dénominateur.** `trend=True` dégrade la
   baseline (MAE 11,75 → 11,85 et 15,05 → 15,48 cm) : le gain relatif monte
   parce que l'étalon baisse, pendant que la MAE du produit stagne. Le gain hors
   biais reste le bon chiffre à publier, mais il n'est **pas** le bon chiffre
   pour arbitrer un changement de baseline — c'est un ratio dont ce changement
   déplace les deux termes. Variante inédite de la leçon du dépôt : mesurer la
   quantité publiée, et pas seulement un ratio la concernant.
3. **Le biais confirme l'argument d'origine, en plus net.** `trend=False` tient
   −0,8 / −0,2 cm contre +1,0 / +1,3 cm — un biais 1,3 à 6× meilleur, dans le
   même sens qu'au 2026-08-04.

**Le risque de dérive n'est pas ce qui condamne `trend=True`, et il faut le dire
avec le chiffre.** Sur les 31 fits rejoués par bras, l'extrapolation de la
tendance sur les 30 jours de péremption vaut 0,1 mm (médiane, Brest) à 1,4 mm
(Saint-Malo), au pire 6 mm : négligeable devant 12–15 cm de MAE de baseline. La
péremption à 30 j a bien désarmé la cicatrice de −0,3 m. Ce qui reste
rédhibitoire est ailleurs : **la pente elle-même n'est pas une tendance**.
|pente| monte à 2·10⁻⁴ m/j, soit 7 cm/an, ~25× le signal séculaire réel
(~3 mm/an à Brest) ; la médiane, elle, est à 3,6·10⁻⁶ m/j (1,3 mm/an) et donc
physique. Sur 730 j, utide ajuste de la variabilité interannuelle du niveau
moyen et l'appelle tendance, puis la reporte du centre de sa fenêtre jusqu'au
temps de service — jusqu'à 8 cm sur le pire fit, et cet offset saute d'un refit
à l'autre. C'est exactement le biais perdu au point 3.

Réserve résiduelle : deux stations, une fenêtre de test d'un an. Un troisième
marégraphe ou une autre année pourrait déplacer les 1,5–1,8 pts de gain, mais
il faudrait un renversement du signe de l'effet sur la MAE **et** sur le biais
pour rouvrir — les deux pointent aujourd'hui dans la même direction.

Reproduction : `docs/archive/` ne porte pas ce script ; la mesure se rejoue en
recalculant les deux baselines depuis les mêmes observations REFMAR (fetch
unique de 1 825 j), via `harmonic.causal_predict` avec `harmonic.fit`
monkeypatché sur `utide.solve(..., trend=<flag>)`, puis `dataset.assemble` et le
chemin `tide` de `scripts/train.py::evaluate` (365 j de test, mêmes candidats
ML, même bootstrap `_gain_confidence_interval`), les deux bras notés sur le même
ré-échantillonnage par jour d'émission.

Implémenté : `harmonic.REFIT_DAYS` (constante partagée backtest/production),
`HarmonicModel.fitted_at` persisté, et `daily` qui marque la station `missing`
au-delà de la péremption.

**Ce que ça économise, mesuré le 2026-08-04 à Brest** : le fetch REFMAR
quotidien passe de 730 jours (17 511 lignes, ~50 requêtes chunkées, **24,3 s**) à
4 jours (87 lignes, une requête, **0,4 s**), auxquels s'ajoute le `utide.solve`
qui ne tourne plus qu'une fois par mois. Soit ~25 s par station de marée et par
jour, ×2 stations.

⚠️ Un chiffre a été retiré ici : « le `daily --dry-run` complet tombe de ~50 s à
2,4 s ». Il ne résiste pas à la vérification — le run complet mesuré après coup a
pris **8 min**, dominé par Candhis et Open-Meteo pour les 8 stations, que ce
chantier ne touche pas. L'économie est réelle mais elle est sur la voie marée,
pas sur le run entier. Même erreur de forme que la cadence choisie sur la
baseline seule : mesurer précisément la portion qu'on a changée, et ne pas
extrapoler au tout.

**Le ré-ajustement est fait par le run lui-même** (`daily._ensure_harmonic`), pas
par un cron séparé. C'est ce qui rend la condition 1 structurelle au lieu de
conventionnelle : la cadence servie en production ne peut plus diverger de celle
que `causal_predict` rejoue, puisqu'il n'y a qu'une constante et qu'un seul
endroit qui décide de rafraîchir. Un cron semestriel séparé aurait été un
deuxième réglage à garder d'accord avec le premier — exactement la famille de
skew que ce chantier existait pour fermer. Coût : ~50 s **une fois par
semestre**, sur le run qui franchit la péremption.

Deux gardes, une seule cadence : `_ensure_harmonic` rafraîchit en amont,
`_baseline_window` refuse en aval. Si le rafraîchissement échoue (REFMAR
indisponible), la station est `missing` — jamais servie sur des constantes
périmées. `scripts/fit_harmonic.py` reste comme CLI d'amorçage d'une station
neuve, et appelle la même fonction de production.

`.github/workflows/daily.yml` commite désormais `pipeline/models` : sans ça le
fit rafraîchi serait jeté avec le runner et repayé à chaque run.

**Anti-causalité assumée du backfill.** Une journée rejouée est reconstruite avec
les constantes *du jour*, donc ajustées sur des observations postérieures à la
journée en question. C'est minuscule — les constantes décrivent un site, pas la
météo d'un jour — et ces journées portent `backfilled`. À rouvrir seulement si
l'historique rejoué sert un jour à *mesurer* quelque chose plutôt qu'à remplir le
graphe : il faudrait alors un fit par journée rejouée, ce que le coût ne
justifie pas aujourd'hui.

---

## Features — les trois dernières, mesurées le 2026-08-04

Les trois pistes ouvertes ont été implémentées ensemble, le jeu reconstruit
**une seule fois** (le rebuild d'une station `tide` coûte un fetch REFMAR de
deux ans plus un `utide.solve` tous les 30 j), puis chacune chiffrée par
ablation sur ce jeu-là. Deux gardées, une jetée.

Protocole, identique pour les trois : `--ablate` (mise à zéro, mêmes lignes,
même split, même graine), puis **bootstrap apparié par jour d'émission**,
2000 tirages, les deux configurations notées sur le même ré-échantillonnage —
sinon l'IC porte sur deux gains bruités indépendants au lieu de porter sur leur
différence. 365 jours d'émission de test, 17 484 lignes.

| feature | brest | saint-malo | verdict |
|---|---|---|---|
| `tide_rate` (phase de marée) | +0,78 pt [+0,41 ; +1,16] | **+4,11 pts** [+2,95 ; +5,23] | **gardée** |
| `dp_dt_3h` / `dp_dt_6h` | **+2,83 pts** [+2,16 ; +3,51] | +0,17 pt [−0,30 ; +0,66] | **gardée** |
| `wind_stress_u` / `_v` | −0,05 pt [−0,37 ; +0,26] | −0,04 pt [−0,38 ; +0,31] | **jetée** |
| les deux gardées, ensemble | +3,49 pts | +4,26 pts | — |

Publié : **brest +50,4 → +53,9 %** (MAE 5,8 → 5,4 cm), **saint-malo
+29,8 → +34,1 %** (10,6 → 9,9 cm). Les deux stations restent PASS, `weak: false`.

Trois faits de méthode valent d'être gardés.

**Les contributions s'additionnent.** +0,78 +2,83 = 3,61 contre 3,49 mesurés
ensemble à brest ; +4,11 +0,17 = 4,28 contre 4,26 à saint-malo. Les deux
features ne se recouvrent donc pas, et la complémentarité suit la physique des
deux sites plutôt qu'un réglage : Brest répond au **déplacement** du système
dépressionnaire, Saint-Malo à la **phase** de sa marée.

**La configuration ablatée reproduit exactement le `gate.json` d'avant le
chantier** (50,36 et 29,83). C'est le contrôle qui manquait aux mesures de
juillet : il vérifie que le jeu reconstruit ce matin et l'ancien mesurent bien
la même chose, et donc que les deltas ci-dessus sont des features et non un
changement de jeu de données.

**Deux bugs ont été trouvés par un test qui échoue, aucun par relecture.** La
pente de marée revenait silencieusement divisée par deux sur la dernière heure
de l'horizon (le voisin le plus proche de « +1 h » y était l'heure elle-même,
dans la tolérance d'alignement d'une heure) ; et la tendance de pression, prise
sur un nombre de lignes plutôt que sur des heures écoulées, n'aurait valu trois
heures que tant que la voie reste horaire. À noter pour la prochaine fois : la
suite est repassée au vert dès les *fixtures* mises à jour — 33 tests cassés par
la même cause mécanique — et ce vert-là ne testait aucune des trois features.

### 1. Tension de vent — mesurée et jetée

`wind_stress_u = wind_u10 * hypot(u,v)`, idem en v. L'argument physique est
juste — la surcote répond à la tension, qui va en U², et donner U à un modèle en
attendant qu'il retrouve U² lui fait dépenser de la capacité sur une
transformation connue d'avance — et il n'a rien acheté : −0,05 pt à brest,
−0,04 à saint-malo, les deux IC95 % à cheval sur zéro, P(Δ≤0) de 63 % et 59 %.

Le code a été retiré. Ce qui reste, et qui est l'information utile pour la
suite : ablater `wind_u10`/`wind_v10` **pendant que la tension était présente**
ne coûtait rien non plus (−0,05 à brest, −0,26 à saint-malo). Les deux paires
sont donc mutuellement substituables — un arbre retrouve U² tout seul à partir
de u et v — et la question « garder les deux ou remplacer » est tranchée par le
bas : on garde la paire brute, qui était déjà là.

**Attention à ne pas sur-lire cette dernière mesure** : elle ne dit pas que le
vent ne sert à rien. Elle a été prise avec la tension en place, donc avec
l'information du vent toujours dans le modèle. Le vent lui-même vaut +3,4 pts à
brest, mesuré le 2026-08-04 (§ Réserves, dette « régression sous la feature
vent »).

### 1bis. Ce qui reste de la piste tension

Rien à faire tel quel. Si on y revient, ce ne sera pas pour la forme de la
transformation mais pour l'axe : un `U²·cos(θ−θ₀)` avec un θ₀ par station a été
écarté d'emblée ici, parce que c'est un paramètre à régler à la main par
station, donc une porte ouverte au sur-ajustement. Cet arbitrage-là n'a pas été
mesuré, il a été refusé sur principe — et il le reste tant que la version sans
axe ne montre rien.

### 2. Tendance de pression (dP/dt) — gardée

`pressure_anom` différenciée sur 3 h et 6 h, hPa/h. **brest +2,83 pts hors
biais** (IC95 % [+2,16 ; +3,51], P(Δ≤0) = 0 %) ; saint-malo +0,17 pt,
indistinguable de zéro (IC95 % [−0,30 ; +0,66], P(Δ≤0) = 24 %). Gardée sur la
règle qui valait déjà pour `mean_err_3h/6h` : une station gagne franchement,
l'autre ne perd pas, et les deux reçoivent les colonnes — une liste de features
par station serait un paramètre à régler à la main.

La réserve annoncée ici (« une différence temporelle sur une série de prévision
ne se comporte pas comme sur une réanalyse ») s'est révélée plus précise que
prévu, et c'est le seul point de conception qui comptait. Le forçage marée est
**stratifié par âge de run** : `forcing_at_issue` sert, pour une même émission,
des lignes issues de runs différents de part et d'autre de chaque frontière de
jour d'échéance. Une différence posée *après* ce narrowing enjambe donc deux
runs deux fois par émission, et lit l'écart run-à-run — 0,44 hPa à `_d1`,
1,40 hPa à `_d2`, soit l'ordre de grandeur d'une vraie tendance de 3 h — comme
de la météo. Aux heures où une dépression bouge le plus, en prime.

La tendance est donc calculée dans le parser (`sources.wind._tide_frame`), à
l'intérieur de chaque bloc de run, **avant** tout narrowing ; le narrowing la
transporte ensuite comme n'importe quelle colonne de forçage. Un test échoue si
quelqu'un l'en sort : il pose des blocs décalés de 20 hPa avec une pente propre
de 1 hPa/h, vérifie que le saut de 21 hPa existe bien dans la frame narrowée,
puis que la tendance y vaut 1 partout — une diff post-narrowing y lirait 7.

Corollaire opérationnel : `fetch_wind_forecast` demande `past_days=1`. Sans un
jour d'historique, un run émis peu après 00:00 UTC servirait des tendances NaN
sur le début de son propre horizon, là où l'entraînement, qui couvre deux ans,
n'en voit jamais — le train/serve skew par la petite porte.

### 3. Interaction marée-surcote — gardée, et c'est le gros morceau

`tide_rate` : dérivée centrée de la prédiction harmonique, m/h — le courant de
marée en première approximation, donc la phase. **saint-malo +4,11 pts hors
biais** (IC95 % [+2,95 ; +5,23], P(Δ≤0) = 0 %), brest +0,78 pt (IC95 %
[+0,41 ; +1,16]). C'est le plus gros gain d'une feature seule à saint-malo
depuis l'ouverture du scoreboard.

L'asymétrie n'est pas une surprise, elle était **prédite et déjà mesurée** : la
réserve « plafond propre à saint-malo » avait établi que le résidu y porte une
composante semi-diurne à phase non stationnaire (autocorrélation qui tombe à
0,17 à 6 h puis remonte à 0,84 à 12 h) qu'aucune feature ne voyait, `hour_sin`
et `hour_cos` étant solaires 24 h. C'est exactement ce que la phase de marée
apporte. À brest, où le résidu est basse fréquence, elle n'avait presque rien à
apprendre — et rapporte en conséquence.

Deux choix d'implémentation, tous deux dictés par le fait qu'une pente est une
quantité plus fragile qu'une valeur :

- **Lue à ±1 h par alignement, pas par `.diff()`.** Les deux voies sont
  horaires aujourd'hui (`waterlevel.fetch_tide_obs` ré-échantillonne REFMAR à
  1 h avant l'ajustement), donc `.diff()` marcherait — mais il voudrait dire
  « une ligne », et une ligne ne vaut une heure que tant que ce resample reste
  où il est.
- **Tolérance d'alignement de 30 min et non d'une heure.** À une heure, le
  voisin le plus proche de « dernière heure de l'horizon + 1 h » est cette heure
  elle-même : la différence revenait divisée par deux, silencieusement, avec les
  unités d'une pente centrée. Trouvé par un test, pas par relecture. La
  dernière heure hérite maintenant de la pente précédente — une ligne sur 48, et
  la même ligne à l'entraînement et au service.

Coût mesuré de la centration : une différence centrée à ±1 h atténue une M2 de
4,2 % (facteur sin(ωΔt)/ωΔt). C'est un facteur constant sur toute la colonne,
donc un changement d'unité et non de forme — sans effet sur un arbre, absorbé
par le coefficient d'une ridge.

### 4. Mémoire plus longue du résidu — ✅ faite le 2026-08-04

`mean_err_3h` / `mean_err_6h`, commit `bf60c04`. saint-malo +3,5 pts (IC95 %
[+2,7 ; +4,4]), brest −0,4 pt, indistinguable de zéro. C'est cette mesure qui a
fixé la règle de décision réutilisée pour les trois ci-dessus.

**Le backlog de features est vide.** Ce qui reste ouvert est plus bas.

---

## Ridge comme plancher honnête — mesuré le 2026-08-05

**Question.** Le scoreboard publie un gain IA contre une baseline physique.
Combien de ce gain un simple linéaire régularisé capte-t-il déjà ? Si `ridge`
fait jeu égal, le gradient boosting est de la complexité non payée et il faut
le dire. `ridge` existait déjà comme candidat (`model._estimator`), mais il
n'était comparé que sur la **validation** (tableau « Comparaison des modèles
ML » de `model-eval.md`) — jamais chiffré sur les folds de test scellés, et
jamais avec une barre d'erreur sur l'**écart**.

**Comment.** `pipeline/scripts/compare_ridge.py` rejoue le protocole
d'évaluation de `train.py` — mêmes origines rolling, même purge 48 h, même
sélection de baseline physique dans le seul passé de chaque origine — une fois
par candidat forcé, puis une fois en sélection automatique (le comportement de
production). Aucun effet de bord : rien n'est promu dans `models/`, et
`docs/model-eval.md`, qui est généré, n'est pas réécrit à la main.

```
cd pipeline && UV_CACHE_DIR=.uv-cache uv run python scripts/compare_ridge.py
```

**Périmètre — les 9 stations, depuis le 2026-08-05.** D'abord mesuré sur les
**5 stations dont le jeu d'entraînement était présent** dans
`pipeline/data_train/` (`brest`, `saint-malo` côté marée, `ouessant`, `dieppe`,
`cherbourg-vent` côté vent) — les 4 stations `wave` (`pierres-noires`,
`belle-ile`, `anglet`, `cherbourg`) manquaient alors de `*_raw.parquet` (bug
Candhis, voir `docs/data-sources.md` § 1). Le bug corrigé et les datasets
houle régénérés (2023-08-06 → 2026-08-05), la réserve de périmètre est
**levée** : les 4 stations houle sont mesurées ci-dessous, sur les mêmes
folds de test scellés régénérés le 2026-08-05.

### Par candidat, sur les folds de test scellés

| Station | Type | Candidat | MAE modèle | Gain hors biais | IC95 % gain | Protocole | Coût protocole (s) | Artefact |
|---|---|---|---|---|---|---|---|---|
| brest | tide | `ridge` | 0.0564 | +51.8 % | [+48.0 ; +55.1] | holdout annuel (1×365 j) | 0.7 | 2 ko |
| brest | tide | `hgb` | 0.0556 | +52.4 % | [+49.1 ; +55.4] | holdout annuel (1×365 j) | 2.8 | 1 084 ko |
| brest | tide | `hgb-per-lead` | 0.0539 | +53.9 % | [+50.8 ; +56.7] | holdout annuel (1×365 j) | 10.5 | 3 247 ko |
| brest | tide | auto (production) → `hgb-per-lead` | 0.0539 | +53.9 % | [+50.8 ; +56.7] | holdout annuel (1×365 j) | 21.3 | 3 247 ko |
| saint-malo | tide | `ridge` | 0.1186 | +21.2 % | [+18.4 ; +23.9] | holdout annuel (1×365 j) | 0.7 | 2 ko |
| saint-malo | tide | `hgb` | 0.1018 | +32.4 % | [+30.0 ; +34.7] | holdout annuel (1×365 j) | 5.1 | 1 084 ko |
| saint-malo | tide | `hgb-per-lead` | 0.0991 | +34.1 % | [+31.6 ; +36.4] | holdout annuel (1×365 j) | 12.9 | 3 248 ko |
| saint-malo | tide | auto (production) → `hgb-per-lead` | 0.0991 | +34.1 % | [+31.6 ; +36.4] | holdout annuel (1×365 j) | 20.2 | 3 248 ko |
| ouessant | wind | `ridge` | 1.0809 | +11.9 % | [+10.3 ; +13.3] | rolling-origin multi-saisons (4×90 j) | 21.6 | 2 ko |
| ouessant | wind | `hgb` | 0.9690 | +21.0 % | [+19.2 ; +22.7] | rolling-origin multi-saisons (4×90 j) | 28.7 | 1 091 ko |
| ouessant | wind | `hgb-per-lead` | 0.9831 | +19.9 % | [+18.0 ; +21.6] | rolling-origin multi-saisons (4×90 j) | 36.8 | 2 867 ko |
| ouessant | wind | auto (production) → `hgb` | 0.9690 | +21.0 % | [+19.2 ; +22.7] | rolling-origin multi-saisons (4×90 j) | 40.3 | 1 091 ko |
| dieppe | wind | `ridge` | 0.8744 | +29.0 % | [+26.3 ; +31.3] | rolling-origin multi-saisons (4×90 j) | 19.4 | 2 ko |
| dieppe | wind | `hgb` | 0.8084 | +34.3 % | [+31.9 ; +36.5] | rolling-origin multi-saisons (4×90 j) | 25.8 | 1 091 ko |
| dieppe | wind | `hgb-per-lead` | 0.8222 | +33.2 % | [+30.8 ; +35.3] | rolling-origin multi-saisons (4×90 j) | 34.6 | 2 786 ko |
| dieppe | wind | auto (production) → `hgb` | 0.8084 | +34.3 % | [+31.9 ; +36.5] | rolling-origin multi-saisons (4×90 j) | 47.1 | 1 091 ko |
| cherbourg-vent | wind | `ridge` | 1.1759 | +17.6 % | [+14.8 ; +20.5] | rolling-origin multi-saisons (4×90 j) | 20.2 | 2 ko |
| cherbourg-vent | wind | `hgb` | 1.0818 | +24.2 % | [+21.5 ; +26.8] | rolling-origin multi-saisons (4×90 j) | 26.4 | 1 091 ko |
| cherbourg-vent | wind | `hgb-per-lead` | 1.0902 | +23.6 % | [+20.9 ; +26.2] | rolling-origin multi-saisons (4×90 j) | 41.9 | 3 266 ko |
| cherbourg-vent | wind | auto (production) → `hgb` | 1.0856 | +24.0 % | [+21.2 ; +26.6] | rolling-origin multi-saisons (4×90 j) | 48.1 | 1 091 ko |

Le « coût protocole » est le temps mur du protocole **entier** (assemblage des
données + tous les folds + refit production), pas un temps de fit unitaire.
Sur les stations `wind`, ~20 s sont de l'assemblage commun à tous les
candidats : le surcoût propre au boosting y est de l'ordre de +6 à +20 s. Sur
`brest`, où l'assemblage est négligeable, le rapport est net : **0,7 s et 2 ko
pour `ridge`, 10,5 s et 3 247 ko pour `hgb-per-lead`** — ×15 en temps, ×1 600
en taille d'artefact.

### L'écart, avec sa barre d'erreur

Deux IC95 % qui se chevauchent ne concluent rien sur un écart mesuré aux mêmes
heures. Le tableau ci-dessous est donc un bootstrap **apparié** : mêmes lignes
de test, même baseline débiaisée au dénominateur, rééchantillonnage de **jours
d'émission entiers** — jamais de lignes, les 48 leads d'un run n'étant pas
indépendants.

| Station | Type | Incumbent | Gain `ridge` | Gain incumbent | Δ (incumbent − ridge) | IC95 % de l'écart | Conclusion |
|---|---|---|---|---|---|---|---|
| brest | tide | `hgb-per-lead` | +51.8 % | +53.9 % | +2.1 pt | [+0.7 ; +3.5] pt | boosting payé |
| saint-malo | tide | `hgb-per-lead` | +21.2 % | +34.1 % | +12.9 pt | [+10.7 ; +15.1] pt | boosting payé |
| ouessant | wind | `hgb` | +11.9 % | +21.0 % | +9.1 pt | [+7.7 ; +10.6] pt | boosting payé |
| dieppe | wind | `hgb` | +29.0 % | +34.3 % | +5.4 pt | [+4.4 ; +6.3] pt | boosting payé |
| cherbourg-vent | wind | `hgb` | +17.6 % | +24.0 % | +6.3 pt | [+4.9 ; +7.7] pt | boosting payé |

### Conclusion

**Le gradient boosting est payé sur les 5 stations mesurées : sur chacune, sa
borne IC95 % basse d'écart contre `ridge` est strictement positive.** Le modèle
servi en production **n'est donc pas changé**.

Nuances qui restent vraies :

* `brest` est le cas limite : +2,1 pt seulement, IC95 % [+0,7 ; +3,5]. Le
  plancher linéaire y capture **96 % du gain publié** (51,8 / 53,9) pour ~1/15
  du temps et 1/1600 de la taille d'artefact. Si la marée devait un jour
  tourner sur un budget contraint, c'est la station où basculer coûterait le
  moins.
* `saint-malo` est l'inverse exact : `ridge` n'y capte que 62 % du gain. C'est
  la station où la non-linéarité paie le plus, ce qui est cohérent avec
  l'interaction marée-surcote documentée plus haut (§ Features, point 3).
* `hgb-per-lead` ne se justifie **que** sur les `tide`. Sur les trois stations
  `wind`, il est battu par `hgb` simple tout en pesant 2,5× plus lourd — la
  sélection automatique le rejette déjà, et c'est bien ce qu'elle doit faire.
* Le tableau « Comparaison des modèles ML » de `model-eval.md` reste, lui, sur
  la **validation** : c'est voulu, c'est la fenêtre où la sélection a le droit
  de regarder. Les chiffres ci-dessus sont sur le **test**, et ne s'y
  substituent pas.

---

## Ridge sur les 4 stations houle — mesuré le 2026-08-05, réserve de périmètre levée

**Rejoué avec `pipeline/scripts/compare_ridge.py --station pierres-noires,belle-ile,cherbourg,anglet`**
sur les datasets régénérés le 2026-08-05 (bug Candhis corrigé, voir
`docs/data-sources.md` § 1). Même protocole que ci-dessus, mêmes garanties
(rien n'est promu dans `models/`, `docs/model-eval.md` n'est pas touché).

**Toutes les 4 stations sont en holdout dégradé** (1 à 3 folds sur 4 au lieu
des 4 attendus — l'historique houle n'a pas encore 730 j de profondeur, voir
`docs/review_codex_2026-08-05.md` § 6), donc `evaluation_ready = false` :
**aucune n'est publiable dans ce protocole en l'état**, y compris belle-ile
et son +25,7 %. Ceci est indépendant de la question ridge/boosting posée ici.

### Par candidat, sur les folds de test scellés (dégradés)

| Station | Type | Candidat | MAE modèle | Gain hors biais | IC95 % gain | Protocole | Coût protocole (s) | Artefact |
|---|---|---|---|---|---|---|---|---|
| pierres-noires | wave | `ridge` | 0.2136 | +19.8 % | [+16.1 ; +22.9] | holdout dégradé (3×90 j) | 51.3 | 2 ko |
| pierres-noires | wave | `hgb` | 0.2233 | +16.1 % | [+9.2 ; +21.4] | holdout dégradé (3×90 j) | 67.7 | 1 091 ko |
| pierres-noires | wave | `hgb-per-lead` | 0.2306 | +13.4 % | [+5.8 ; +19.6] | holdout dégradé (3×90 j) | 70.3 | 2 611 ko |
| pierres-noires | wave | auto (production) → `hgb` | 0.2079 | +21.9 % | [+18.0 ; +25.2] | holdout dégradé (3×90 j) | 223.9 | 1 091 ko |
| belle-ile | wave | `ridge` | 0.1258 | +25.7 % | [+22.8 ; +28.3] | holdout dégradé (3×90 j) | 72.5 | 2 ko |
| belle-ile | wave | `hgb` | 0.1435 | +15.2 % | [+9.2 ; +20.4] | holdout dégradé (3×90 j) | 79.0 | 1 090 ko |
| belle-ile | wave | `hgb-per-lead` | 0.1545 | +8.8 % | [+1.0 ; +15.2] | holdout dégradé (3×90 j) | 96.4 | 2 840 ko |
| belle-ile | wave | auto (production) → `ridge` | 0.1258 | +25.7 % | [+22.8 ; +28.3] | holdout dégradé (3×90 j) | 91.4 | 2 ko |
| anglet | wave | `ridge` | 0.0989 | +6.4 % | [+1.2 ; +10.9] | holdout dégradé (**1×90 j**) | 78.1 | 2 ko |
| anglet | wave | `hgb` | 0.1062 | −0.5 % | [−6.4 ; +4.8] | holdout dégradé (**1×90 j**) | 94.0 | 1 089 ko |
| anglet | wave | `hgb-per-lead` | 0.1109 | −4.9 % | [−11.6 ; +1.1] | holdout dégradé (**1×90 j**) | 93.4 | 2 565 ko |
| anglet | wave | auto (production) → `ridge` | 0.0989 | +6.4 % | [+1.2 ; +10.9] | holdout dégradé (**1×90 j**) | 92.3 | 2 ko |
| cherbourg | wave | `ridge` | 0.0971 | +21.7 % | [+16.8 ; +26.4] | holdout dégradé (2×90 j) | 72.8 | 2 ko |
| cherbourg | wave | `hgb` | 0.1077 | +13.1 % | [+7.2 ; +18.1] | holdout dégradé (2×90 j) | 80.7 | 1 091 ko |
| cherbourg | wave | `hgb-per-lead` | 0.1126 | +9.1 % | [+2.6 ; +14.5] | holdout dégradé (2×90 j) | 96.0 | 3 265 ko |
| cherbourg | wave | auto (production) → `ridge` | 0.0971 | +21.7 % | [+16.8 ; +26.4] | holdout dégradé (2×90 j) | 93.0 | 2 ko |

Anglet ne tient qu'**une seule origine exploitable** (`train 12484 / test 4260
rows, 1 origin(s)`) : les trois autres origines sont écartées avant même
d'atteindre le test (train/test dégénéré ou validation interne trop courte).
Pierres-noires est le seul cas où `auto` bat tous les candidats forcés pris
individuellement (+21,9 % contre +19,8/+16,1/+13,4 %) — la sélection choisit
le meilleur candidat *par origine*, ce qu'aucun candidat unique forcé sur
toutes les origines ne peut reproduire.

### L'écart, avec sa barre d'erreur

| Station | Type | Incumbent | Gain `ridge` | Gain incumbent | Δ (incumbent − ridge) | IC95 % de l'écart | Conclusion |
|---|---|---|---|---|---|---|---|
| pierres-noires | wave | `hgb` | +19.8 % | +21.9 % | +2.1 pt | [+1.3 ; +3.1] pt | boosting payé |
| belle-ile | wave | `ridge` | +25.7 % | +25.7 % | +0.0 pt | [+0.0 ; +0.0] pt | boosting **non** payé |
| cherbourg | wave | `ridge` | +21.7 % | +21.7 % | +0.0 pt | [+0.0 ; +0.0] pt | boosting **non** payé |
| anglet | wave | `ridge` | +6.4 % | +6.4 % | +0.0 pt | [+0.0 ; +0.0] pt | **indéterminé** |

Belle-ile et cherbourg : l'incumbent **est** `ridge` — la sélection
automatique a déjà rejeté le boosting sur validation, l'écart nul sur test le
confirme sans le trancher une seconde fois. Anglet : l'IC95 % à largeur nulle
[+0,0 ; +0,0] est l'artefact d'un bootstrap par jour d'émission mené sur **un
seul groupe rééchantillonnable** (1 origine) — un intervalle qui ne peut pas
varier n'est pas une mesure de zéro, c'est l'absence de mesure. **Indéterminé,
pas nul** : conclure demande ~9 mois de mesure continue de plus pour qu'une
2ᵉ origine atteigne les 90 j de train requis. Baisser `VAL_DAYS_CAP` pour y
arriver artificiellement est explicitement refusé (déplacer les poteaux).

### Conclusion — le résultat s'inverse par rapport aux tide/wind

Sur `tide`/`wind`, le boosting est payé aux **5** stations mesurées (borne
IC95 % basse toujours strictement positive). **Sur `wave`, il n'est payé qu'à
une station sur quatre** (pierres-noires) : sur belle-ile et cherbourg, la
sélection automatique retient déjà `ridge` — le boosting avait perdu un étage
plus tôt, à la validation, pas seulement sur ce test. **Sur la houle, le
plancher linéaire EST le modèle servi**, aux deux tiers des stations
mesurables. Anglet reste hors conclusion, faute de mesure possible.

**Aucun chiffre publié ne bouge.** Ce chantier mesure, il ne promeut rien :
`models/` et `gate.json` restent ceux du run daily en cours ; les 9
`model_name` publiés le 2026-08-05 (voir plus haut) sont ceux déjà en
production, pas ceux de cette table.

### Bug corrigé pour rendre cette mesure possible : asymétrie de `train.evaluate`

La découpe de validation interne ne vivait que dans `if len(model_names) > 1`
: une origine dégénérée n'était donc jetée que lors de la sélection
automatique (`auto`), jamais quand un seul candidat était forcé — deux
protocoles différents pour `auto` et pour `ridge` forcé, sur des lignes de
test différentes, rendant tout bootstrap **apparié** entre les deux invalide.
Sans ce correctif, le tableau « L'écart » ci-dessus n'aurait mesuré rien de
cohérent. Cause : les premières origines des stations houle n'ont que
~60-80 j d'émission assemblables, moins que la fenêtre de validation de 90 j
— jamais déclenché sur `tide`/`wind` (~550-820 j d'historique). Correctif :
la décision (garder/jeter une origine) sort du bloc conditionnel — une
origine inutilisable l'est désormais pour **tous** les candidats — et elle
est nommée (`skipped_origins` dans la sortie de `evaluate`, jamais propagé à
`gate.json`, un contrat publié). Non-régression prouvée par dump avant/après
sur brest, ouessant, cherbourg-vent : seule différence, `"skipped_origins": []`
— tout le reste identique bit à bit.

---

## Réserves ouvertes

- ~~**Divergence validation/test sur saint-malo.**~~ **Fermée le 2026-08-04 —
  c'était le protocole, pas la station.** Les chiffres de la réserve
  (validation +8,8 % / test −11,7 %) avaient été pris contre la baseline
  harmonique 90 j et sur un test de 30 jours : les deux sont morts. Sur la
  baseline actuelle l'écart s'inverse et devient énorme aux **deux** stations —
  brest +9,4 % val → +53,3 % test (×5,7), saint-malo +5,2 % → +28,0 % (×5,4).
  Il n'est donc pas spécifique à saint-malo. Trois causes mécaniques cumulées :

  1. `VAL_DAYS_CAP = 120` place toujours la validation sur avril-août, le creux
     annuel de surcote. Le modèle final, restreint aux mêmes mois du test, ne
     fait que +26,3 % (brest) et +19,1 % (saint-malo), contre +61,6 % et
     +31,0 % le reste de l'année.
  2. Le modèle de sélection est ajusté sur 2024-02 → 2025-04, donc sur **un
     seul** printemps-été. Sur les mois avril-juillet du test il ne rend que
     +6,7 % (brest) et +11,7 % (saint-malo).
  3. C'est un tirage unique de 4 mois : les gains mensuels de validation de
     brest sont +26,2 / +20,3 / −27,5 / −9,3 %.

  **Conséquence pratique : le gain de validation est une borne basse
  structurelle, pas un pronostic du test.** Il ne sert qu'à classer trois
  candidats, et il remplit ce rôle (`hgb-per-lead` gagne aux deux stations sur
  la validation comme sur le test). Ne jamais le lire comme une estimation de
  skill, et ne pas s'alarmer d'un écart val/test de facteur 5 : c'est la valeur
  attendue de ce protocole.

- ~~**Plafond propre à saint-malo**~~ **Fermée le 2026-08-05 — sans piste,
  diagnostic exploratoire épuisé.** Script `pipeline/scripts/diag_saint_malo_ceiling.py`
  (déterministe, IC95 bootstrap par jour d'émission, 2000 réplications,
  candidat figé a priori à `hgb-per-lead` — le test scellé est décrit, jamais
  sélectionné dessus). Quatre pistes, toutes éliminées :

  1. **Le secteur « flot début » n'existe pas.** Pire secteur de saint-malo =
     flot plein (ratio 0,722) ; pire secteur de brest, station sans plafond =
     flot début (0,503) ; dispersion max/min identique aux deux stations
     (×1,14 contre ×1,16) ; l'ordre s'inverse sur le pli antérieur (contrôle
     hors test scellé). Tous les IC95 par secteur recouvrent celui de « TOUS ».
  2. **Non-stationnarité saisonnière et en amplitude, pas en phase.**
     ⟨|Z|⟩ = 16,53 cm à saint-malo (brest 4,63 cm), partie stationnaire
     |⟨Z⟩| = 1,37 cm → rapport 0,083 : aucune raie stationnaire à extraire.
     ⟨|Z|⟩ passe de 13,1 cm (juin) à 20,2 cm (mars) — c'est saisonnier, pas
     calé sur un état de marée qu'une feature pourrait lire.
  3. **Ce n'est pas la baseline.** Décalage temporel optimal δ* = 0 min aux
     deux stations (y compris restreint au secteur « flot début ») ; gain
     d'amplitude ajusté α = 0,9989, la baseline n'est pas mal calibrée en
     amplitude. Correction de phase de marée hors échantillon (ajustée sur le
     passé du test, appliquée dessus) : −0,14 % [−0,78 % ; +0,48 %], à cheval
     sur zéro — aucun gain à en attendre.
  4. **Le plafond est structuré, pas du bruit d'observation.** Plancher de
     bruit (obs+baseline) : MAE 2,5 à 4,8 cm, très en dessous des 9,97 cm de
     MAE du modèle publié — il reste donc un signal réel à côté du bruit.
     Mais un oracle **parfait** (qui connaîtrait la composante semi-diurne
     exacte du cycle courant) ne vaudrait que +33,5 %, et un oracle **causal**
     (qui n'utilise que le dernier cycle complet avant l'instant courant,
     amorti) vaut **−3,6 % [−6,2 % ; −1,2 %]** : négatif. L'information
     causalement exploitable est déjà captée par `mean_err_3h`/`mean_err_6h`
     — il n'y a rien de plus à aller chercher dans le résidu passé.

  **Conclusion : réserve fermée, sans piste.** Les trois candidats de feature
  qu'un futur chantier pourrait être tenté de rouvrir — dérivée de la marée,
  mémoire du résidu, marnage — sont chacun épuisés par un point ci-dessus
  (respectivement 3, 4, et le point 1/Q2 pour le marnage). **Ne pas rouvrir ce
  point sur une feature dérivée de la marée, de la mémoire du résidu ou du
  marnage.** Un nouveau diagnostic, pas une feature de plus, serait le seul
  chemin valide.

  Seule feature à espérance positive sortie de ce diagnostic : concerne
  **brest**, pas saint-malo — l'oracle causal y vaut **+1,8 %** (IC95
  [+1,2 % ; +2,4 %]), soit 0,10 cm. Trop petit pour justifier une colonne de
  plus, noté pour mémoire seulement.

  ---

  **Historique du diagnostic (2026-08-04, avant fermeture) :**

  *ouverte, mais pour la première fois avec une piste mesurée*. Éliminés le
  2026-08-04 : la vitesse de marée `|dh/dt|` (gain plat sur les quintiles,
  corr +0,04), le marnage vives-eaux/mortes-eaux (corr +0,04, gain plat de 4,4
  à 10,9 m), et toute raie harmonique stationnaire résiduelle (M2/S2/M4/M6/K1 :
  < 0,1 % de la variance du résidu, amplitudes < 1 cm — la baseline 730 j ne
  laisse pas de constituante mal ajustée). Ce qui reste : à brest le résidu est
  basse fréquence (87 % de sa variance survit à une moyenne glissante de 25 h,
  autocorrélation 0,86 à 6 h), à saint-malo non — 48 %, et une autocorrélation
  qui **s'effondre à 0,17 à 6 h puis remonte à 0,84 à 12 h**. C'est une
  composante semi-diurne à phase non stationnaire, qu'aucune feature actuelle ne
  peut capter puisque `hour_sin`/`hour_cos` sont solaires 24 h, et l'erreur du
  modèle en garde la signature (−0,26 à 6 h, +0,60 à 12 h). C'est l'appui mesuré
  de l'item « 3. Interaction marée-surcote » ci-dessus.

  **Mise à jour du 2026-08-04, la réserve recule sans se fermer.** La phase de
  marée était la piste que ce diagnostic désignait, et elle a tenu ce qu'il
  annonçait : **+4,11 pts**, MAE modèle **0,106 → 0,099 m**. L'écart à brest
  passe de ×2,0 à ×1,84 (0,099 contre 0,054). Ce qui reste ouvert est donc plus
  étroit qu'avant et plus difficile : la piste que le diagnostic nommait a été
  jouée, et il reste 4,5 cm d'écart entre les deux stations sans candidat
  identifié. Rouvrir ce point demandera un nouveau diagnostic, pas une feature
  de plus dans la liste — celle-ci est vide.

  **Diagnostic refait le 2026-08-04 sur le modèle publié : la signature n'a pas
  bougé d'un pouce.** Autocorrélation de l'erreur du modèle le long de l'horizon,
  365 jours d'émission de test, saint-malo :

  | série | 1 h | 3 h | 6 h | 9 h | 12 h | 18 h | 24 h |
  |---|---|---|---|---|---|---|---|
  | résidu baseline | +0,91 | +0,46 | +0,13 | +0,36 | +0,83 | +0,07 | +0,68 |
  | erreur modèle **sans** `tide_rate` | +0,81 | +0,17 | −0,26 | +0,01 | +0,70 | −0,27 | +0,55 |
  | erreur modèle **actuel** | +0,83 | +0,18 | **−0,26** | −0,00 | **+0,68** | −0,27 | +0,56 |

  `tide_rate` a retiré de l'**amplitude** (MAE 0,1052 → 0,0991 m) et **rien de la
  structure** : à 6 h et à 12 h, l'erreur du modèle actuel est indiscernable de
  celle du modèle d'avant. Contrôle de méthode : l'autocorrélation le long de
  l'horizon et celle de la série continue en temps valide coïncident à 0,01 près,
  et le résidu de baseline reproduit bien les chiffres historiques de cette
  réserve (0,13 contre 0,17 à 6 h ; 0,83 contre 0,84 à 12 h).

  **La conséquence est plus utile que la mesure.** Le modèle tient déjà la phase
  de marée complète — `baseline` lui donne h, `tide_rate` lui donne dh/dt — et il
  s'en sert (c'est +4,11 pts). Si la composante semi-diurne restante lui échappait
  *malgré ça*, c'est qu'elle n'est pas calée sur la marée. Vérifié en
  conditionnant l'erreur sur la phase de marée reconstruite à partir de ces deux
  colonnes (8 secteurs) : la MAE du modèle varie bien de 0,089 à 0,115 m selon le
  secteur, mais celle du **résidu de baseline** varie dans les mêmes proportions,
  et le rapport des deux reste plat — **0,63 à 0,73 partout**. Le modèle suit donc
  l'amplitude du signal secteur par secteur ; il ne laisse pas de gisement calé
  sur la marée.

  **Ce que ça élimine** : toute feature dérivée de la prédiction harmonique.
  L'axe « interaction marée-surcote » est épuisé, pas seulement joué. La
  composante restante est semi-diurne **sans être calée sur la marée locale** —
  ce que disait déjà le mot « non stationnaire » de la réserve d'origine, et qui
  est maintenant une conclusion mesurée plutôt qu'une hypothèse.

  **Le seul secteur qui dépasse** : « flot début », rapport 0,73 contre 0,63 à
  0,67 ailleurs. C'est petit et c'est le seul candidat visible. Non expliqué.

  Enfin, l'écart brest/saint-malo n'est pas un défaut propre à saint-malo dans
  cette lecture-là : brest montre la même dispersion par secteur (×1,23 contre
  ×1,28) et la même insensibilité de sa structure d'erreur à `tide_rate`. Ce qui
  distingue les deux stations reste l'**échelle** du résidu, pas la manière dont
  le modèle échoue.
- ~~**Dette brest « régression sous la feature vent ».**~~ **Close le 2026-08-04,
  ablation rejouée : le vent aide, il ne dégrade plus.** Sur la baseline 90 j
  (Task 7B), le vent coûtait −7,7 pts à brest (+2,3 % sans vent → −5,4 % avec
  vent) — la mesure qui avait ouvert cette réserve. Rejouée sur la baseline
  actuelle (`fb35db7`, harmonique 730 j + forçage stratifié) : le vent apporte
  **+3,4 pts hors biais** (+49,9 % → +53,3 %, MAE 0,0596 → 0,0556), écart
  confirmé par bootstrap par jour d'émission (IC95 % [+1,7 %, +4,9 %], jamais
  sous zéro sur 2000 tirages) et par un contrôle à modèle ML figé — le gain
  tient qu'on force `hgb-per-lead` ou `ridge`, ce n'est donc pas un artefact de
  sélection de candidat. Saint-malo confirme la même direction en plus faible
  (+1,6 pt, IC95 % [+0,5 %, +2,6 %]). L'hypothèse retenue le 2026-07-30
  (« station abritée, dataset court ») n'avait pas besoin d'être vraie : le
  signal était noyé dans la dérive saisonnière de la baseline 90 j, pas dans le
  vent lui-même.

  Détail à garder pour la tension de vent (§ Features n°1) : sur brest,
  `wind_v10` porte l'essentiel de l'effet (−3,2 pts à l'ablation) et `wind_u10`
  presque rien (−0,6 pt). L'effet absolu reste petit — 0,4 cm de MAE — la dette
  portait sur le *signe*, et c'est le signe qui s'est inversé.
- ~~**Skew ERA5 restant dans `backfill.py`.**~~ **Résolu le 2026-08-04** :
  `backfill.py` passe par le même forçage stratifié, et `build_features` narrow
  par émission — un jour rejoué est donc forcé par le run qu'il aurait vraiment
  eu, comme une journée en direct.

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
