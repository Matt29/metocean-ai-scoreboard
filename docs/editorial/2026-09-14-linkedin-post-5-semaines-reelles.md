# Post LinkedIn — « 5 semaines réelles contre le backtest »

**Date de rédaction** : 2026-09-14
**Statut** : **prêt à poster**. Les chiffres portent sur une fenêtre fermée
(émissions du 2026-08-05 au 2026-09-08) : ils ne bougent plus. Une fois posté,
figer ce fichier comme archive.
**Angle** : premier bilan des prévisions **réellement servies**, confronté au
backtest qui a autorisé la mise en ligne de ces modèles précis. Trois résultats
concordent, deux font moins bien, un fait mieux, un échoue et sort.
**Cahier des charges** :
[`docs/superpowers/specs/2026-08-05-axe-editorial-design.md`](../superpowers/specs/2026-08-05-axe-editorial-design.md)
(déclencheur n°5, « station qui bascule publiée ↔ non publiée », pour anglet).
**Figure** : [`figure-5-semaines-reel-vs-backtest.png`](figure-5-semaines-reel-vs-backtest.png)
— à joindre au post. Régénérable :
`cd pipeline && uv run --with matplotlib python scripts/figure_live_vs_backtest.py`.

---

## Texte du post — prêt à copier-coller

<!-- DÉBUT DU POST -->

Un backtest ne prouve rien tant que le modèle n'a pas tourné pour de vrai. Voici 5 semaines de prévisions réellement servies.

Chaque matin depuis le 5 août, mon post-traitement IA corrige la prévision physique à 48 h sur 8 stations françaises : vent, houle, niveau de la mer. Rien n'est rejoué. La prévision part, l'observation arrive, l'écart est archivé.

Sur la figure, pour chaque station : le gain mesuré en backtest avant la mise en ligne, et le gain réel sur les prévisions servies. Même métrique, mêmes modèles. Le backtest, c'est le modèle entraîné sur le passé puis jugé sur une période qu'il n'a jamais vue : pour le vent et la houle, les 30 derniers jours avant la mise en ligne (juillet) ; pour la marée, les 365 derniers.

Ce qui tient. Cherbourg (vent) : +25 % en réel, +25 % en backtest. Les Pierres Noires (houle) : +26 % et +26 %. Ouessant et Dieppe restent à 4 points de leur promesse.

Ce qui fait moins bien. Brest : +22 % en réel contre +54 % en backtest. Saint-Malo : +23 % contre +34 %. Ce sont les deux stations de marée, et leur backtest couvre une année entière, tempêtes d'hiver comprises. Cinq semaines d'été laissent peu de surcote à corriger. C'est mon explication, je ne l'ai pas encore mesurée.

Ce qui fait mieux. Belle-Île : +33 % en réel contre +14 % en backtest. Je n'ai pas d'explication, et je ne vais pas en inventer une.

Ce qui échoue. Anglet : −6 %. En réel, l'IA dégrade la prévision physique. Elle avait passé un ancien critère, plus permissif. Le protocole actuel exige quatre saisons de test, elle n'en a qu'une. Elle est dépubliée aujourd'hui.

Sur les 7 stations restantes, l'IA bat la physique de 20 à 33 %, et elle gagne 27 à 33 journées selon la station, sur 31 à 35 servies.

Réserve : 5 semaines, un seul été, pas d'intervalle de confiance sur la période réelle. C'est un bilan, pas un verdict.

Scoreboard public, prévisions et observations avec.

https://oceandataconsulting.fr/scoreboard

<!-- FIN DU POST -->

**Longueur** : ~2 450 signes espaces compris.

---

## Les chiffres employés, avec leur source

**Réel** : `data/<station>/history.json` sur `origin/main` @ `3e2b1ae` (daily du
2026-09-13), jours `status = "ok"` **non** `backfilled`, émissions du
2026-08-05 au 2026-09-08, MAE pondérée par heure scorée. Calcul :
`pipeline/scripts/figure_live_vs_backtest.py`, exécuté le 2026-09-14.

**Nature du backtest** (vérifié dans `docs/model-eval.md` aux révisions d'entraînement) :
vent `29b10df` et houle `a8e8950` → « test = les 30 derniers jours d'émission »
(données jusqu'au ~2026-08-02, donc juillet 2026), une seule fenêtre, sans
intervalle de confiance ; marée `81a60d3` → « test = les 365 derniers jours
d'émission ». Même mécanique partout : split temporel, sélection du modèle sur
une validation prise dans le train, test vu une seule fois.

**Backtest** : champ `gain` (MAE brute, **pas** `gain_debiased`) du
`pipeline/models/gate.json` à `81a60d3`. Pourquoi cette révision et cette
fenêtre : les modèles marée sont remplacés le 2026-08-04 après l'émission du
jour, et la release pics `84a4473` remplace marée et vent dès l'émission du
2026-09-09. Entre les deux, aucun artefact de modèle ne change : chaque jour de
la fenêtre a été servi par les modèles que ce gate a jugés.

| Station | Jours | Heures | MAE IA | MAE physique | Gain réel | Gain backtest | Jours gagnés |
|---|---|---|---|---|---|---|---|
| Brest (marée) | 35 | 1 680 | 0,078 m | 0,100 m | +22,0 % | +54,2 % | 28 |
| Saint-Malo (marée) | 31 | 1 488 | 0,130 m | 0,168 m | +22,5 % | +34,2 % | 30 |
| Ouessant (vent) | 35 | 1 680 | 1,032 m/s | 1,294 m/s | +20,2 % | +23,8 % | 33 |
| Dieppe (vent) | 35 | 1 680 | 0,876 m/s | 1,117 m/s | +21,6 % | +18,1 % | 32 |
| Cherbourg (vent) | 35 | 1 680 | 1,089 m/s | 1,458 m/s | +25,3 % | +25,4 % | 27 |
| Les Pierres Noires (houle) | 33 | 1 483 | 0,147 m | 0,198 m | +25,9 % | +25,8 % | 28 |
| Belle-Île (houle) | 34 | 1 593 | 0,105 m | 0,156 m | +32,7 % | +14,1 % | 29 |
| Anglet (houle) | 34 | 1 593 | 0,119 m | 0,113 m | −5,7 % | +9,8 % | 14 |

Correspondance avec le texte :

- « 8 stations » : les 8 ci-dessus. Cherbourg houle (jamais publiée) est exclue.
- « 5 semaines », « depuis le 5 août » : fenêtre 2026-08-05 → 2026-09-08.
- « Ouessant et Dieppe restent à 4 points » : écarts de 3,6 et 3,5 points.
- « de 20 à 33 % » : +20,2 % (Ouessant) à +32,7 % (Belle-Île).
- « 27 à 33 journées, sur 31 à 35 servies » : 27 (Cherbourg vent) à 33 (Ouessant) ; 31 (Saint-Malo) à 35 jours servis.
- Anglet « ancien critère » : entrée `gate.json` d'avant le protocole
  multi-saisons (`pass: true`, sans `evaluation_ready`). Ré-entraînement du
  2026-09-14 (`d3cfae9`) : `holdout dégradé`, une origine, `pass: false`.
  « Une saison » = une seule origine de test de 90 jours.
- Explication marée : **raisonnée, non mesurée**, et le post le dit.

**Réserves à ne pas perdre en relecture**

- Un premier jet de ce bilan comparait les prévisions servies au `gate.json`
  **actuel**. Faux : les stations vent y ont des gains de backtest de modèles
  entrés en service le 2026-09-09 (Dieppe +37,5 % au lieu de +18,1 %). Le
  premier jet concluait donc à tort que le réel décevait partout.
- Il comparait aussi le gain réel brut au gain **hors biais** du gate. Deux
  métriques différentes.
- Houle : les 4 bouées Candhis sont muettes depuis le 2026-09-08 12:00 (panne
  côté Cerema, vérifiée le 2026-09-14). La fenêtre s'arrête avant, le post ne
  la mentionne pas.
- « Dépubliée aujourd'hui » suppose le push de `d3cfae9` et le daily suivant.
  Poster après.
