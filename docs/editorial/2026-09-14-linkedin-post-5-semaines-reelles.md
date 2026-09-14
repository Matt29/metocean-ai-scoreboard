# Post LinkedIn — « L'IA bat la physique sur 7 stations sur 8 »

**Date de rédaction** : 2026-09-14
**Statut** : **posté le 2026-09-14. Archive figée : ne plus modifier le texte.**
**Angle** : une seule question, celle que se pose le lecteur — sur des
prévisions réellement servies, l'IA bat-elle la prévision physique ? Oui sur 7,
non sur 1, et celle-là sort.
**Cahier des charges** :
[`docs/superpowers/specs/2026-08-05-axe-editorial-design.md`](../superpowers/specs/2026-08-05-axe-editorial-design.md)
(déclencheur n°5, « station qui bascule publiée ↔ non publiée », pour anglet).
**Figure** : [`figure-5-semaines-ia-vs-physique.png`](figure-5-semaines-ia-vs-physique.png)
— à joindre au post. Régénérable :
`cd pipeline && uv run --with matplotlib python scripts/figure_ia_vs_physique.py`.

---

## Texte du post — prêt à copier-coller

<!-- DÉBUT DU POST -->

Mon IA bat-elle la prévision météo-océanique physique ? Pas en backtest : sur 5 semaines de prévisions réellement servies.

Chaque matin depuis le 5 août, elle corrige la prévision physique à 48 h sur 8 stations françaises : vent, houle, niveau de la mer. Rien n'est rejoué. La prévision part, l'observation arrive, l'écart est archivé.

Résultat : 7 stations sur 8 battues.

L'erreur moyenne baisse de 20 % à Ouessant, jusqu'à 33 % à Belle-Île. Brest et Dieppe −22 %, Saint-Malo −23 %, Cherbourg −25 %, Les Pierres Noires −26 %.

La huitième, Anglet, fait pire que la physique : +6 % d'erreur. Elle avait passé un ancien critère de mise en ligne, trop permissif. Le critère actuel exige quatre saisons de test ; elle n'en a qu'une. Elle est retirée du site.

C'est un premier bilan, pas un verdict : 5 semaines d'été, sans intervalle de confiance. Le chiffre se remet à jour chaque matin.

Scoreboard public, prévisions et observations avec.

https://oceandataconsulting.fr/scoreboard

<!-- FIN DU POST -->

**Longueur** : ~1 100 signes espaces compris.

---

## Les chiffres employés, avec leur source

**Réel** : `data/<station>/history.json` sur `origin/main` @ `3e2b1ae` (daily du
2026-09-13), jours `status = "ok"` **non** `backfilled`, émissions du
2026-08-05 au 2026-09-08, MAE pondérée par heure scorée. Calcul :
`pipeline/scripts/figure_ia_vs_physique.py`, exécuté le 2026-09-14. La colonne
« Gain backtest » (champ `gain` du `gate.json` @ `81a60d3`, celui des modèles
qui ont servi ces jours-là) n'est pas dans le post : contexte seulement.

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
- « 5 semaines », « depuis le 5 août » : fenêtre 2026-08-05 → 2026-09-08. Elle
  commence après le ré-entraînement marée du 2026-08-04 et s'arrête avant la
  release `84a4473` : un seul jeu de modèles sur toute la fenêtre.
- « 7 stations sur 8 battues » : gain réel > 0 sur 7 stations.
- Les pourcentages du post sont les gains réels arrondis à l'unité.
- Anglet « +6 % d'erreur » : MAE IA 0,119 m contre 0,113 m (gain −5,7 %).
- Anglet « ancien critère » : entrée `gate.json` d'avant le protocole
  multi-saisons (`pass: true`, sans `evaluation_ready`). Ré-entraînement du
  2026-09-14 (`d3cfae9`) : `holdout dégradé`, une origine de test de 90 jours
  (« une saison »), `pass: false`.

**Réserves à ne pas perdre en relecture**

- « Retirée du site » suppose le daily qui suit le push de `d3cfae9`. Poster
  après.
- Houle : les 4 bouées Candhis sont muettes depuis le 2026-09-08 12:00 (panne
  côté Cerema, vérifiée le 2026-09-14). La fenêtre s'arrête avant, le post ne
  la mentionne pas.
- Un premier jet mettait en regard réel et backtest ; figure jugée illisible le
  2026-09-14. Au passage, deux erreurs corrigées : comparaison au `gate.json`
  actuel (backtests de modèles entrés en service le 2026-09-09) et au gain hors
  biais (autre métrique).
