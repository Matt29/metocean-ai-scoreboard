# Post LinkedIn — « 40 jours réels contre le backtest : le vent »

**Date de rédaction** : 2026-09-14
**Statut** : **brouillon, à relire**. Avant envoi, recalculer le tableau des
sources sur la `history.json` du jour (le nombre de jours servis augmente
chaque matin), puis figer ce fichier comme archive.
**Angle** : premier bilan des prévisions **réellement servies** depuis la mise
en ligne, confronté au chiffre du backtest qui a autorisé la publication.
L'écart est défavorable sur les trois stations et le post le dit.
**Cahier des charges** :
[`docs/superpowers/specs/2026-08-05-axe-editorial-design.md`](../superpowers/specs/2026-08-05-axe-editorial-design.md)
(déclencheur n°5, « station qui bascule publiée ↔ non publiée », pour anglet).
**Figure suggérée** : aucune, ou la page station Dieppe (courbe IA / physique /
observation sur 48 h).

---

## Texte du post — prêt à copier-coller

<!-- DÉBUT DU POST -->

40 jours de prévisions de vent réellement servies. Voici ce qu'elles valent face au backtest qui les a mises en ligne.

Depuis le 4 août, chaque matin à 6 h UTC, mon post-traitement IA corrige la prévision de vent à 10 m d'ARPEGE sur 48 h pour trois stations Météo-France : Ouessant, Dieppe, Cherbourg. Rien n'est rejoué. La prévision part, l'observation arrive, l'écart est archivé.

Erreur absolue moyenne, IA contre ARPEGE, sur 1 904 heures par station :

Ouessant : 1,01 contre 1,25 m/s, soit −19 %. L'IA gagne 36 jours sur 40.
Dieppe : 0,86 contre 1,10 m/s, soit −22 %. 37 jours sur 40.
Cherbourg : 1,03 contre 1,42 m/s, soit −27 %. 32 jours sur 40.

Le backtest promettait mieux partout : −22 %, −38 % et −34 % sur la même métrique. Dieppe perd 15 points entre le banc d'essai et la réalité.

Deux lectures, et je ne sais pas encore laquelle est la bonne. Le backtest couvre quatre saisons et ces 40 jours sont un seul été : ce n'est pas le même vent. Ou le modèle vaut réellement moins en production qu'au banc. 40 jours ne permettent pas de trancher, et je n'ai pas d'intervalle de confiance sur la période réelle. Le critère de mise en ligne, lui, reste celui du backtest.

Ce que ces 40 jours confirment en revanche : trois stations validées, trois stations qui battent la physique en réel, presque tous les jours.

Et le contre-exemple, côté houle. Anglet était publiée sur un ancien critère, moins exigeant. En réel, elle perd : +5 % d'erreur par rapport au modèle physique, 16 jours gagnés sur 36. Le protocole actuel, quatre saisons de test, la refusait. Elle est dépubliée depuis ce matin.

Scoreboard public, prévisions et observations avec.

https://oceandataconsulting.fr/scoreboard

<!-- FIN DU POST -->

**Longueur** : ~1 900 signes espaces compris (à recompter après relecture).

---

## Les chiffres employés, avec leur source

Réel : calculé le 2026-09-14 sur `origin/main` @ `3e2b1ae` (daily du
2026-09-13), `data/<station>/history.json`, jours `status = "ok"` **non**
`backfilled`, MAE pondérée par le nombre d'heures scorées. Backtest :
`pipeline/models/gate.json` des stations vent, release `84a4473` du
2026-09-09 (non touchées par le ré-entraînement houle du 2026-09-14).

| Chiffre du post | Valeur exacte | Source |
|---|---|---|
| « Depuis le 4 août », « 40 jours » | 40 jours servis, 2026-08-04 → 2026-09-12, 0 `missing`, 1 904 heures par station | `history.json` |
| Ouessant 1,01 / 1,25 m/s, −19 %, 36/40 | MAE IA 1,010, référence 1,252, gain 19,3 %, 36 jours où `mae_ia < mae_baseline` | idem |
| Dieppe 0,86 / 1,10 m/s, −22 %, 37/40 | 0,859 / 1,104, 22,1 %, 37/40 | idem |
| Cherbourg 1,03 / 1,42 m/s, −27 %, 32/40 | 1,034 / 1,419, 27,1 %, 32/40 (station `cherbourg-vent`) | idem |
| « ARPEGE » | `baseline_model = meteofrance_arpege_europe` sur tous les jours servis et dans le gate | `history.json`, `gate.json` |
| Backtest −22 %, −38 %, −34 % « sur la même métrique » | champ `gain` (MAE brute, **pas** hors biais) : 0,2174 / 0,3754 / 0,3362. Gains hors biais publiés par le gate : 21,0 / 34,3 / 24,0 % | `gate.json` |
| « quatre saisons » | `rolling-origin multi-saisons`, 4 × 90 j, 360 jours d'émission de test | `gate.json` |
| « Dieppe perd 15 points » | 37,5 − 22,1 = 15,4 points de gain brut | calcul |
| « pas d'intervalle de confiance sur la période réelle » | aucun bootstrap n'est calculé sur `history.json` | — |
| Anglet « +5 % d'erreur », 16/36 | MAE IA 0,115, référence 0,110 m, 36 jours servis 2026-08-03 → 2026-09-07, 16 gagnés | `history.json` @ `3e2b1ae` |
| Anglet « ancien critère » / « le protocole actuel la refusait » | entrée `gate.json` d'avant le protocole multi-saisons (`pass: true`, sans `evaluation_ready`) ; ré-entraînement du 2026-09-14 : `holdout dégradé`, 1 origine, `pass: false` | `docs/plan-dev-modele.md` « Houle sur 3 ans » |

**Réserves à ne pas perdre en relecture**

- Les jours servis l'ont été par **les versions successives** des modèles vent
  (ré-entraînements d'août et release pics du 2026-09-09), pas par un artefact
  unique. Le post dit « mon post-traitement », pas « ce modèle ».
- L'explication saisonnière est **raisonnée, non mesurée**. Le post la présente
  comme une lecture possible parmi deux, pas comme la cause.
- Comparer le gain réel au gain **hors biais** du gate serait faux (métriques
  différentes). Un premier jet de ce bilan l'a fait et concluait à tort que
  Cherbourg faisait mieux en réel qu'au banc.
- Houle : les 4 bouées Candhis sont muettes depuis le 2026-09-08 12:00 (panne
  côté Cerema, vérifiée le 2026-09-14). Anglet n'a donc que 36 jours servis. Le
  post ne mentionne pas la panne.
- Le post « pics » du 2026-09-09 (non encore posté) affirme « les quatre
  stations de houle n'ont pas de verdict » : faux depuis le 2026-09-14
  (pierres-noires et belle-ile ont une entrée `peaks`, alerte PASS). À corriger
  avant de le poster.
