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
   inutile de la conception.
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
**Réserve honnête** : cet arbitrage n'a pas été re-mesuré à 30 jours de
péremption. L'argument d'origine (l'offset de −0,3 m) venait d'un fit figé bien
plus vieux, et extrapoler une tendance sur 30 jours n'est pas l'extrapoler sur
six mois. À rouvrir si on veut récupérer ces 2,6 points.

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
chantier ne touche pas. L'économie est réelle mais elle est sur la jambe marée,
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
heures que tant que la jambe reste horaire. À noter pour la prochaine fois : la
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

- **Lue à ±1 h par alignement, pas par `.diff()`.** Les deux jambes sont
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

- **Plafond propre à saint-malo** (MAE modèle 0,112 m contre 0,056 à brest) —
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

  Ce qui n'a **pas** été fait et qui serait le premier geste : refaire
  l'autocorrélation du résidu **du modèle actuel** à saint-malo. Les chiffres
  ci-dessus (0,17 à 6 h, 0,84 à 12 h) décrivent le résidu de la baseline, avant
  que la phase de marée n'entre dans le modèle. Si la signature semi-diurne a
  disparu de l'erreur résiduelle, le plafond restant est d'une autre nature et
  la description ci-dessus a cessé de le décrire.
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
