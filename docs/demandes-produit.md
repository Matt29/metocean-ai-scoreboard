# Demandes produit en attente

Demandé par Matthieu le 2026-08-03. Ce fichier n'est pas un plan : c'est le cadrage
et surtout les **conditions préalables**, parce que les deux demandes butent sur la
même chose — ce que les données autorisent aujourd'hui à affirmer.

---

## 1. Graphiques enrichis : séries temporelles, prévisions, écart IA / physique

**Demande.** « De beaux graphiques qui montrent les séries temporelles, les
prévisions, et l'écart entre IA et modèle, et comment ça améliore. »

### Ce qui existe déjà

`src/sections/scoreboard/StationChart.jsx` (dépôt du site) trace, par station, les
7 derniers jours *scorés* : observation, IA, prévision physique, puis la prévision
+48 h en cours. Il interrompt le trait sur une journée sans donnée au lieu de
l'enjamber. `DailyMaeTable.jsx` donne les MAE quotidiennes.

Il manque : une vue d'ensemble multi-stations, l'évolution de l'écart dans le
temps (aujourd'hui seul l'agrégat 30 j est affiché), et une lecture de la
distribution de l'erreur plutôt que de sa seule moyenne.

### Le blocage, à lire avant de dessiner quoi que ce soit

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

### Préalable

Attendre d'avoir des jours scorés à partir de prévisions réellement émises la
veille (`backfilled: false`). Le premier arrivait le 2026-08-04. Compter une
dizaine de jours avant qu'une courbe d'écart veuille dire quelque chose.

### Quand ce sera le cas

- Distinguer visuellement les jours reconstitués des jours opérationnels. Les
  mélanger dans une même courbe est le piège principal : la partie reconstituée
  est systématiquement plus flatteuse.
- Montrer la dispersion, pas seulement la moyenne : la MAE seule masque les
  épisodes où l'IA dégrade la prévision.
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

Le handoff est explicite : le levier le plus fort n'est pas l'algorithme mais le
**ré-entraînement sur les prévisions archivées**. Le modèle actuel est entraîné
sur du vent de réanalyse ERA5 et servi avec du vent prévu ARPEGE — les chiffres
sont donc optimistes par construction. La collecte a démarré le 2026-08-03
(`pipeline/data_forecast_archive/`, un Parquet par jour, colonne `source`).

À rejouer au même moment (déjà analysé, voir le handoff) :
- Tester d'autres modèles : une ridge comme plancher honnête, un modèle par lead.
- La bascule vers `~/Documents/DEV/meteodata_hub` (AROME 1,3 km) pour le
  téléchargement quotidien. Conclusion actuelle : le hub n'a aucune réanalyse donc
  ne remplace pas ERA5 à l'entraînement, et télécharger des GRIB entiers pour
  6 points est disproportionné.

Reste aussi ouverte une dette non expliquée : **la régression de brest sous la
feature vent** (l'hypothèse « maille ERA5 contaminée par la terre » a été testée
et infirmée).

---

## Ordre suggéré

1. Vérifier les premiers jours scorés non reconstitués.
2. ~~Étendre la mesure aux échéances 25–48 h.~~ Fait le 2026-08-03 (`pending`
   + `_rescore_pending`).
3. Ré-entraîner sur les prévisions archivées (≥ 1 mois de collecte).
4. Alors seulement : les graphiques (1) sur des chiffres opérationnels, et la
   décision d'horizon (2) sur un modèle dont on connaît le skill réel.
