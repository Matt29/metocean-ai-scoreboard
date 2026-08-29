# Demandes produit en attente

Demandé par Matthieu le 2026-08-03. Ce fichier n'est pas un plan : c'est le cadrage
et surtout les **conditions préalables**, parce que les deux demandes butent sur la
même chose — ce que les données autorisent aujourd'hui à affirmer.

---

## 1. Graphiques enrichis : séries temporelles, prévisions, écart IA / physique — ✅ soldée le 2026-08-05

**Demande.** « De beaux graphiques qui montrent les séries temporelles, les
prévisions, et l'écart entre IA et modèle, et comment ça améliore. »

**Cette section décrivait le pipeline d'avant le 2026-08-04 (analyse MFWAM +
vent ERA5 de réanalyse pour les jours reconstitués) et n'a pas été recalée à
temps.** C'est la cause directe de deux explications fausses données le
2026-08-05 en s'appuyant dessus. Le paragraphe « Le blocage » ci-dessous reste
pour mémoire, barré, plutôt que réécrit sans trace — le mécanisme réel et à
jour est documenté dans
[`docs/biais-forcage-jours-reconstitues.html`](biais-forcage-jours-reconstitues.html).

### Ce qui existe déjà

`src/sections/scoreboard/StationChart.jsx` (dépôt du site) trace, par station, les
7 derniers jours *scorés* : observation, IA, prévision physique, puis la prévision
+48 h en cours. Il interrompt le trait sur une journée sans donnée au lieu de
l'enjamber. `DailyMaeTable.jsx` donne les MAE quotidiennes.

**Livré le 2026-08-05 (T4a)** : la distinction visuelle jours reconstitués /
opérationnels — trame diagonale sur les périodes reconstituées dans
`StationChart.jsx`, légende explicite en toutes lettres, libellé factuel compté
sur la fenêtre affichée (7/30/90 j). Le badge « Reconstitué » du tableau
quotidien existait déjà.

**Livré le 2026-08-05 (T1)** : les deux sous-features restantes.
`compute_lead_breakdown` (pipeline) émet désormais `p50_ia`/`p90_ia`/
`p50_baseline`/`p90_baseline` par tranche d'échéance (quantiles de l'erreur
absolue, `np.quantile` method="linear") — moustaches p50→p90 sur
`LeadTimeChart.jsx`, forme et non couleur seule. `compute_scores` émet
`daily_30d` (MAE quotidienne sur 30 j par station) — nouvelle colonne
« Tendance 30 j » avec `Sparkline.jsx` dans `ScoreboardTable.jsx`, vue
d'ensemble multi-stations sans charger un `history.json` par station. Champs
additifs, `schema_version` inchangé. La mention « reconstitué » est propagée
aux deux, au même seuil que `causalNotes.backfill-ceiling`.

**§1 est soldée** : les trois sous-features (distinction reconstitué/
opérationnel, dispersion de l'erreur, vue multi-stations) sont livrées.

### ~~Le blocage, à lire avant de dessiner quoi que ce soit~~ (périmé, voir ci-dessus)

Au 2026-08-03, **les 29 jours d'historique sont tous reconstitués**
(`backfilled: true`) : produits avec l'analyse MFWAM et le vent ERA5 de réanalyse,
c'est-à-dire des grandeurs connues *après coup*. Ils reproduisent presque
exactement les gains d'entraînement (pierres-noires +65,1 % contre +65,8 % ;
belle-ile +49,2 contre +50,1 ; brest +18,9 contre +22,9 ; anglet +3,8 contre
+5,7). C'est **le plafond reproduit, pas une performance opérationnelle**.

Un graphique intitulé « comment l'IA améliore la prévision » construit sur ces
points-là vendrait un skill qui n'est pas démontré. Trois itérations de
modélisation ont déjà été passées à débusquer des gains qui n'en étaient pas
(une baseline harmonique qui dérivait et fabriquait un faux +48,9 % ; un gain qui
n'était que du débiaisage ; une feature pression retirée après mesure).
L'honnêteté de la mesure **est** l'argument du produit.

**Ce qui a changé depuis :** la voie ERA5 a été supprimée le 2026-08-04 (le
modèle ne s'entraîne plus jamais sur une réanalyse) et la voie marée est passée
à un forçage stratifié par âge de run (Previous Runs API). Les voies houle et
vent restent volontairement sur l'archive « runs frais » — pas un oubli, une
mesure : le gain publié est un rapport, et le biais y tombe des deux côtés
(numérateur et dénominateur), donc il s'annule presque. Mécanisme et mesure
complets : `docs/biais-forcage-jours-reconstitues.html`. Le reste de ce
paragraphe (chiffres MFWAM/ERA5 du 2026-08-03) ne décrit plus le pipeline actuel.

### Préalable

Attendre d'avoir des jours scorés à partir de prévisions réellement émises la
veille (`backfilled: false`). Le premier arrivait le 2026-08-04. Compter une
dizaine de jours avant qu'une courbe d'écart veuille dire quelque chose.

### Quand ce sera le cas

- ~~Distinguer visuellement les jours reconstitués des jours opérationnels.~~
  **Fait (T4a, 2026-08-05).** Les mélanger dans une même courbe était le piège
  principal : la partie reconstituée est systématiquement plus flatteuse.
- ~~Montrer la dispersion, pas seulement la moyenne.~~ **Fait (T1, 2026-08-05)** —
  `p50_ia`/`p90_ia`/`p50_baseline`/`p90_baseline` dans `by_lead`.
- Le gain **hors biais** est le chiffre à mettre en avant, pas le gain brut (voir
  `docs/model-eval.md` : sur 4 stations sur 6, plus de la moitié de l'écart
  disparaît si l'on retire à la baseline son seul biais moyen).
- Ne pas coder en dur noms ni comptes de stations : ils viennent de `gate.json`
  via `stations.json`, et le gate évolue. Le module `src/data/scoreboard.js`
  expose déjà `gateNote(rows)` pour la prose dérivée.
- Contrainte design : `~/Documents/DEV/WEB/ODC_WEBSITE/DESIGN_SYSTEM/` (skill
  `oceandata-design`), alias sémantiques uniquement. Le verdict passe toujours par
  un libellé texte, jamais par la couleur seule — c'est une exigence
  d'accessibilité déjà tenue partout dans le scoreboard.

---

## 2. Brique « nowcasting » — prévision plus poussée à J+24 h

**Demande.** « Ajouter une brique nowcasting, faire une prévi plus poussée
J+24 h. »

### Ce qui est déjà en ligne (précision du 2026-08-03)

La prévision J+24 h — et même J+48 h — **est déjà publiée et affichée** :
`latest.json` porte la série complète jusqu'à +48 h (`BASELINE_HORIZON_H` dans
`daily.py`) et `StationChart.jsx` la trace (« la prévision +48 h en cours, que
rien n'a encore vérifiée »). La demande ne porte donc pas sur *produire* cette
échéance : elle existe. Ce qui manque est sa **vérification** (ci-dessous) et,
séparément, l'éventuel nowcasting 0–6 h.

### Ce que c'est réellement

Pas un ajout de front : une **itération de modélisation**. Deux réserves de
cadrage, dans cet ordre.

**Le vocabulaire.** « Nowcasting » désigne l'horizon 0–6 h, appuyé sur
l'observation qui vient d'arriver. « J+24 h » est de la prévision à courte
échéance. Les deux ne se construisent pas pareil : le nowcasting vit ou meurt sur
la latence d'ingestion des observations (Candhis, SHOM/REFMAR), la prévision 24 h
sur la qualité du forçage atmosphérique. Trancher lequel est visé avant de coder.

**L'état du scoreboard.** ~~Le pipeline ne score même pas les échéances
25–48 h aujourd'hui~~ — **résolu le 2026-08-03** : les leads non couverts par
les obs au moment du scoring partent en `pending` dans l'entrée `history.json`
et sont complétés par les runs suivants quand leurs obs arrivent
(`daily._rescore_pending`). `max_lead_h` monte vers 48 en ~2 jours ; les tout
premiers jours complets arrivent donc autour du 2026-08-05.

### Préalable

~~Scorer l'horizon existant jusqu'à 48 h~~ — fait (ci-dessus). Reste le
préalable de la section 1 : des jours scorés non reconstitués, en nombre.

### Le vrai levier, une fois là

**Périmé depuis le 2026-08-04, voir `docs/biais-forcage-jours-reconstitues.html`
pour l'état réel.** Ce paragraphe (2026-08-03) décrivait un skew ERA5-train /
ARPEGE-serve sur le vent. Ce skew n'existe plus : la voie ERA5 a été supprimée
le 2026-08-04, plutôt que corrigée — le modèle ne s'entraîne plus jamais sur
une réanalyse, houle et vent comme marée. Le levier qui reste n'est donc plus
« sortir de la réanalyse » mais l'axe déjà tranché ailleurs dans ce document :
attendre des jours scorés à partir de prévisions réellement émises la veille
(`backfilled: false`, préalable de § 1) pour que les métriques publiées
cessent de reproduire le plafond d'entraînement.

Ridge comme plancher honnête et modèle par lead : **fait, mesuré le
2026-08-05** — voir `docs/plan-dev-modele.md` § Ridge comme plancher honnête.

Reste ouverte : la bascule vers `~/Documents/DEV/meteodata_hub` (AROME 1,3 km)
pour le téléchargement quotidien. Conclusion à la date de sa dernière mesure :
le hub n'a aucune réanalyse et télécharger des GRIB entiers pour 6 points est
disproportionné — non réévaluée depuis la suppression de la voie ERA5, qui
change la prémisse (il n'y a plus de réanalyse à remplacer).

La dette « régression de brest sous la feature vent » (l'hypothèse « maille
ERA5 contaminée par la terre » avait été testée et infirmée) est **close le
2026-08-04** — voir `docs/plan-dev-modele.md` § Réserves ouvertes : le vent
apporte désormais +3,4 pts hors biais à brest, la cause était la baseline
dérivante, pas le vent.

---

## 3. Stations de vent — points clés côtiers et sites EMR

**Demande** (2026-08-03). Ajouter quelques stations de vent à des points
stratégiques (proches côtes, sites EMR) en récupérant les observations des
stations Météo-France.

### ✅ Cadrage tranché le 2026-08-04

| Question | Décision |
|---|---|
| Rôle du vent | Variable **scorée** à part entière : nouveau `kind = "wind"`, pas une simple vérification de features. |
| Grandeur notée | **Vitesse `FF` seule** (m/s, moyenne 10 min). Un scalaire, comme Hs — toute la chaîne existante se réutilise. La direction reste feature, jamais cible. |
| Baseline | **Meilleur des 3 modèles de vent Open-Meteo par station**, choisi à l'entraînement, écrit dans l'artefact — mécanique `marine-best` des stations houle. Valeur TOML : `wind-best`. |
| Premier lot | **3 stations, une par critère** : un cap/île exposé, la plus proche d'un site EMR, une co-localisée avec une station houle publiée. |
| Hors périmètre | Rafales (`FXI`), direction comme cible, nowcasting. |

L'angle produit reste ce qu'il était : la sélection « sites EMR » est un argument
de prospection, donc une décision commerciale autant que technique — d'où le
lot à 3 points couvrant trois arguments distincts plutôt qu'une façade entière.

### Sondage effectué le 2026-08-03 (rapport : `.superpowers/sdd/2026-08-03-retrain-multi-modeles/sondage-bouees-mf.md`)

Auth vérifiée (header `apikey`, `public-api.meteofrance.fr`, token valide
jusqu'en 2029). Verdicts, mesurés en non-null sur requêtes réelles :

- **Vent : faisable.** 2 151 stations DPObs (RADOME) ; candidates proches des
  stations houle : Biarritz (9,5 km d'Anglet), Cherbourg-Homet (3,9 km) ;
  zones EMR couvertes. Latence ~2 min (compatible scoring 09:30 UTC). Quotas
  ~50-60 req/min, un fetch quotidien est négligeable.
- **Houle : la donnée existe, la couverture manque** (corrigé après relecture
  de la doc par Matthieu). `/liste-bouees` + `/bouees` fonctionnent avec la
  même clé : Hs/période/direction horaires non-null, rétention 24 h, pas
  d'archive. Mais 9 bouées seulement — 8 en Méditerranée, 1 en Atlantique
  (Gascogne, 327 km d'Anglet, 556 km de Cherbourg) → aucun renfort possible
  pour les stations actuelles. À ressortir si des stations Méditerranée (ou
  un produit EMR flottant Med) entrent au scoreboard.
- ~~**Limite historique** : temps réel = fenêtre glissante ~3-4 jours ;
  l'archive longue relève d'une autre API (climatologie, non souscrite). Pour
  entraîner un jour sur ces vents, commencer à archiver tôt ou souscrire.~~
  **Démenti le 2026-08-04, voir ci-dessous.**

### ✅ L'historique d'obs est disponible — le préalable « archiver d'abord » n'existe pas

Contrairement aux bouées (demande 4), le vent **n'a aucun compteur à lancer**.
Deux routes d'archive mesurées le 2026-08-04, sur requêtes réelles :

- **API DPClim** — souscrite par Matthieu le 2026-08-04, clé
  `METEOFRANCE_DPCLIM_API_KEY` dans le `.env` racine (elle couvre *aussi* DPObs ;
  l'ancienne `METEOFRANCE_API_KEY` reste 403 sur DPClim). Mesuré sur
  Ouessant-Stiff (29155005), année 2025 : **8 758 h de `FF` non-null sur 8 760**.
- **Fichiers ouverts data.gouv.fr**, *sans aucune clé* (dataset
  `6569b4473bedf2e7abad3b72`, `BASE/HOR/H_<dep>_*.csv.gz`) : `FF` **100 %**
  non-null sur les stations RADOME côtières du 29, à jour à **J-1**, historique
  remontant aux décennies précédentes.

**Les deux sources d'obs sont la même mesure** : croisement DPObs temps réel vs
archive climato sur 12 h communes à Ouessant, **écart max 0,0 m/s**, directions
identiques. Il n'y a donc pas de skew train/serve sur l'observation — à ne pas
confondre avec le skew ERA5/ARPEGE du *forçage*, lui bien réel (§ 2).

Trois pièges DPClim, chacun mesuré et chacun coûteux s'il est ignoré :

- le fichier est livré en **HTTP 201**, pas 200 — une boucle de polling qui
  n'accepte que 200 jette la charge utile ;
- il n'est **livré qu'une fois** (`410 production déjà livrée` ensuite) : écrire
  sur disque dès la réception ;
- **1 an maximum par commande** (`400` au-delà) → une commande par station et par
  année ; et CSV à **virgule décimale** (`decimal=','`).

Autres limites de la route temps réel (scoring quotidien) : `station/horaire`
rend **une seule heure par requête** et le bulk `paquet/horaire` répond **404** —
donc ~30 requêtes par station et par jour, tenable à 3 stations sous les quotas
(~50-60 req/min), mais c'est ce qui plaide pour un lot restreint.

Enfin, une honnêteté à tenir dans la prose : un anémomètre côtier à 10 m
au-dessus du **sol** n'est pas le vent au large. Sur un site EMR offshore, la
station la plus proche est un **proxy** — à assumer, jamais à vendre comme
« vent au parc ».

### Préalable

**Aucun.** Le préalable initial (« passe après le ré-entraînement sur prévisions
archivées ») visait le risque qu'un nouveau `kind` hérite d'une chaîne instable.
Ce ré-entraînement est fait pour les vagues depuis le 2026-08-03 (§ « Ordre
suggéré », point 3) et la chaîne — gate, `pending`, backfill — a tourné en cron
depuis. Le lot vent peut donc partir.

---

## 4. Bouées Météo-France = nouvelles stations scorées + carte interactive

**Demande** (2026-08-03, soir ; précisée le même soir). Deux volets :

1. **Intégrer les 9 bouées MF** (8 Méditerranée + Gascogne) **au réseau
   d'observation du scoreboard, comme Candhis** : nouvelles stations `wave` à
   part entière — prévision IA + baseline meilleur modèle physique + gate +
   verdict, vérifiées chaque jour contre l'obs bouée (`/bouees` : Hs,
   période, direction, horaire). Pas un simple affichage d'observations.
2. **Carte interactive sur le dashboard du site** pour voir directement les
   bouées et les stations.

### ✅ Archivage des obs démarré le 2026-08-03

`uv run scoreboard archive-obs` tourne dans le cron quotidien depuis le
2026-08-03 (`sources/mfbuoy.py`, sortie `pipeline/data_obs_archive/`, détail
technique : `docs/data-sources.md` §4quater). Le compteur des ~2-3 mois est
donc lancé — **premier entraînement Med envisageable vers 2026-10/11**.
Deux corrections de cadrage issues du premier run réel :

- **Rétention mesurée ~96 h, pas 24 h** (la doc Confluence se trompe). La
  fenêtre demandée est de 90 h, soit ~3,5 runs de marge : un cron raté n'est
  plus une perte définitive.
- **BOUEE_SARDAIGNE ne sert aucune donnée de houle** (0 non-null sur 76 heures,
  alors que vent/pression/température sont là). Compter sur **8 bouées
  exploitables**, pas 9, tant que le comptage quotidien ne montre pas le
  contraire.

### ✅ Les deux préalables mesurés, tranchés le 2026-08-04

Comptés sur les 4 jours d'archive (`data_obs_archive/`, 713 lignes) :

| WMO | lat | lon | heures | dont Hs non-null |
|---|---|---|---|---|
| 6100001 | 43.37 | 7.85 | 80 | 80 |
| 6100002 | 42.07 | 4.66 | 75 | 75 |
| 6101031 | 41.76 | 7.59 | 80 | 80 |
| 6101032 | 41.60 | 10.20 | 75 | 75 |
| 6101033 | 42.81 | 8.42 | 84 | 84 |
| 6101034 | 41.58 | 5.51 | 79 | 79 |
| **6101035** | 40.49 | 6.69 | 76 | **0** |
| 6101036 | 42.34 | 6.71 | 80 | 80 |
| 6200001 (Gascogne) | 45.23 | -4.97 | 84 | 84 |

**8 bouées exploitables, confirmé** : les 8 servent la houle 100 % des heures
où elles émettent ; 6101035 reste à 0 sur 76 h. À revoir seulement si un
comptage ultérieur la voit émettre.

**Couverture Open-Meteo aux 9 positions : 100 %**, sur 2025-08-01 → 2026-08-01
(8784 h), pour les 5 modèles de vagues *et* les 3 modèles de vent. Le préalable
« sonder la couverture Marine avant d'inscrire une bouée » (plus bas) est donc
levé : aucune bouée n'est disqualifiée par la donnée d'entrée. Méthode :
`scripts/probe_coverage.py` transposé aux positions lues dans l'archive d'obs.

### ✅ Pilote Gascogne préparé sans faux verdict (2026-08-29)

Le mode « baseline seule » n'existe toujours pas : `daily.run` ne fait tourner
que les stations dont le gate passe. Pour préparer le chemin sans fabriquer un
verdict, Gascogne (`6200001`) est inscrite dans `stations.toml` avec
`active = false`. `load_stations()` l'exclut par défaut du gate, du scoreboard
et de `stations.json`; les outils hors ligne peuvent l'inclure explicitement.

Le dispatch `mfbuoy` lit l'archive Parquet committée, jamais une requête API par
station. Le builder choisit la source d'observation configurée, joint Hs aux
historiques multi-modèles Open-Meteo et inclut Gascogne seulement avec
`--include-pilots`. Le compteur d'obs reste donc la seule limite scientifique.

**Conclusion : le chemin technique est prêt, l'activation reste reportée au
premier entraînement honnête (2026-10/11).** Aucun artefact ni entrée de gate
n'est créé avant cette mesure.

Livré côté données : catalogue, séries publiques compactes par WMO, QC
fraîcheur/complétude, pilote Gascogne inactif, dispatch et builder. Reste à faire
au moment de l'entraînement : modèle, gate, verdict et activation ; la carte du
site externe peut désormais consommer directement les JSON publics.

### Le chemin imposé par la rétention de `/bouees`

- **Scoring quotidien : faisable dès le premier jour.** La fenêtre de 24 h
  suffit à vérifier la prévision d'hier — même mécanique que Candhis.
- **Entraînement : bloqué par l'historique d'obs.** Les baselines
  historiques existent (l'archive Open-Meteo Marine couvre la Méditerranée),
  mais il n'y a AUCUNE archive d'obs bouées MF via cette API. Donc :
  **archiver les obs dès maintenant** (fait, cf. ci-dessus : le cron quotidien
  les committe, même mécanique que `data_forecast_archive/`), puis entraîner
  quand ~2-3 mois d'obs sont accumulés. (Le « servir d'abord en baseline
  seule » envisagé ici n'existe pas dans le pipeline — voir ci-dessus.)
- Nouveau module source `sources/mfbuoy.py` (auth `apikey`, quotas ~50-60
  req/min largement suffisants) ; `kind = "wave"` réutilise toute la chaîne
  multi-modèles du chantier 2026-08. Vérifier la couverture Marine API sur
  chaque point Med par sondage non-null avant d'inscrire une bouée.
- **Positions** : `/liste-bouees` les fournit — jamais en dur.

### Carte

Contrainte design system ODC (skill `oceandata-design`, alias sémantiques,
verdict jamais par la couleur seule). Lib carto à cadrer avec la contrainte
site statique pré-rendu (fond de carte, tuiles vs vectoriel embarqué).

### Préalable

Après la mise en prod du ré-entraînement multi-modèles (chantier en cours) et
sa vérification cron. **L'archivage des obs bouées peut démarrer tôt** (il
conditionne la date du premier entraînement Med) ; la carte passe avant ou
avec la demande 1 (graphiques).

---

## 5. Encart causal — expliquer l'écart, pas seulement l'afficher

Demandé le 2026-08-05, à l'issue du chantier filtres/widget/OG. **Livré le
2026-08-05.**

### Ce qui existait déjà

La page station avait déjà un encart pour le cas « gain surtout dû à une
correction de biais » (`weak`) et un verdict explicite « Suivie — l'IA n'y bat
pas encore la physique » pour les stations sous le gate.

### Ce qui a été livré

Un encart **causal** (repo site ODC) : fonction pure `causalNotes()` dans
`src/data/scoreboard.js`, composant `src/sections/scoreboard/CausalNotes.jsx`,
rendu dans `ScoreboardStationPage.jsx`, tests dans `scoreboard.test.mjs`. Trois
règles déterministes à seuils, chacune prouvée par un chiffre affiché, aucune
cause météo affirmée :

1. dégradation par échéance — gain(H+6) − gain(H+48) ≥ 15 points de pourcentage ;
2. plafond du backfill — `n_days_backfilled / n_days` ≥ 50 % ;
3. journée extrême — jour le plus récent présent dans `extremes.json`, ou MAE
   ≥ 1,5× la médiane, avec un minimum de 5 jours scorés.

Zéro LLM, zéro nouveau champ pipeline — exactement la matière que
`by_lead`/`by_lead_90d` et les extrêmes publiés donnaient déjà.

---

## Ordre suggéré

1. Vérifier les premiers jours scorés non reconstitués.
2. ~~Étendre la mesure aux échéances 25–48 h.~~ Fait le 2026-08-03 (`pending`
   + `_rescore_pending`).
3. ~~Ré-entraîner sur les prévisions archivées~~ — **fait (2026-08-03)** pour
   les vagues : retrain multi-modèles Task 7, baseline vague basculée sur
   Open-Meteo Marine (meilleur des 5 modèles par station), CMEMS/MFWAM
   retiré du pipeline (voir `docs/data-sources.md` § 4ter). **Mise à jour
   2026-08-03** : plus besoin d'attendre un mois de collecte propre —
   l'API Historical Forecast d'Open-Meteo sert `meteofrance_arpege_europe`
   (vraies prévisions, format identique au live) complet depuis ~2025 (vérifié
   par sondage, station Brest). Limites vérifiées le même jour : leads courts
   seulement (concaténation des runs les plus frais), et la Previous Runs API
   (leads stratifiés 1–7 j) n'a **pas** ARPEGE — seulement `ecmwf_ifs025`.
   **Mise à jour 2026-08-04** : le skew ERA5-train/ARPEGE-serve du vent est
   fermé par suppression de la voie ERA5 (plus de réanalyse à l'entraînement,
   sur aucun `kind`) — voir § 2 ci-dessus et
   `docs/biais-forcage-jours-reconstitues.html`. Auto-hébergement du vent
   reste une option pour lever les quotas si le volume l'exige, indépendamment
   de ce point.
4. Alors seulement : les graphiques (1) sur des chiffres opérationnels et la
   décision d'horizon (2) sur un modèle dont on connaît le skill réel.
5. **Les stations de vent (3) sont sorties de cette file le 2026-08-04** : leur
   seul préalable supposé était l'absence d'historique d'obs, et il est démenti
   (mesures en § 3). Elles n'attendent plus rien — lot en cours.
