# Post LinkedIn — « mon IA écrase les pics, et c'est normal »

**Date de rédaction** : 2026-09-09
**Statut** : **à poster**. Avant envoi, revérifier les chiffres contre le
`gate.json` du jour et `docs/plan-dev-modele.md` § Pics ; si un retrain est
passé entre-temps, mettre à jour le tableau des sources ci-dessous, puis
figer ce fichier comme archive (voir le post du 2026-08-05 pour la règle).
**Angle** : un résultat négatif mesuré — le modèle médian ne restitue que
60 à 80 % de l'amplitude des pics — et la réponse choisie : ne pas toucher au
gate, ajouter deux sorties jugées à part, publier leurs refus.
**Cahier des charges** :
[`docs/superpowers/specs/2026-08-05-axe-editorial-design.md`](../superpowers/specs/2026-08-05-axe-editorial-design.md)
(déclencheur n°3, « réserve fermée ou résultat négatif », et n°5, « station
qui bascule publiée ↔ non publiée » — ici une sortie, pas une station).
**Figure suggérée** : capture de la page station cherbourg-vent avec la bande
p90 et l'encart « Alerte 48 h », ou aucune figure. Pas de capture du tableau
de détection tant qu'il n'a pas 30 jours scorés.

---

## Texte du post — prêt à copier-coller

<!-- DÉBUT DU POST -->

Mon post-traitement IA écrase les pics. Je l'ai mesuré, et ce n'est pas un bug.

Sur les deux hivers du backtest, quand la prévision physique annonce un décile supérieur, l'IA bat la physique sur 8 stations sur 9. Mais elle ne restitue que 60 à 80 % de l'amplitude de la correction observée. Un modèle entraîné en erreur quadratique estime une moyenne conditionnelle : quand le forçage est incertain, donc en tempête, il se replie vers le centre. C'est la fonction de perte, pas le candidat.

Deux réponses possibles. Pondérer les tempêtes dans l'entraînement, ce qui ferait échouer mon propre critère de mise en ligne, et je l'ai refusé. Ou ajouter deux sorties jugées à part, avec leur propre adversaire et leur propre verdict.

Une alerte : la probabilité de dépasser le p90 climatologique de la station dans les 48 h. Jugée au Brier skill contre la climatologie, intervalle à 95 % par jour d'émission, et il faut détecter au moins autant que la physique seuillée. Une borne haute : le quantile 90 de l'observation, jugé à sa couverture, entre 85 et 95 %.

Résultat sur les cinq stations de marée et de vent : l'alerte passe partout. La borne passe à trois. Ouessant couvre 84,9 %, Dieppe 84,0 % : sous 85 %, elles sont publiées comme non publiées, avec le chiffre. Déplacer le seuil d'un point aurait fait passer les deux. Je ne l'ai pas fait.

Saint-Malo passe l'alerte au Brier et détecte moins d'un événement sur deux, avec autant de fausses alertes. Le chiffre est en ligne aussi.

Les quatre stations de houle n'ont pas de verdict : leur historique ne permet pas encore quatre saisons de test.

Un détail qui m'a trompé un mois : ma page « extrêmes » sélectionne les jours au maximum observé. Toute prévision y est mécaniquement en dessous. Ce n'était pas l'IA qui sous-estimait, c'était ma sélection.

Le seuil est climatologique, pas opérationnel : à Dieppe, 8 m/s à 10 m est une brise soutenue. L'alerte dit « journée du décile supérieur », pas « tempête ». Le seuil est affiché à côté de chaque pourcentage pour qu'on ne lise jamais l'un sans l'autre.

Scoreboard public, méthode et données avec.

https://oceandataconsulting.fr/scoreboard

<!-- FIN DU POST -->

**Longueur** : 2 152 signes espaces compris (2 109 hors URL).

---

## Les chiffres employés, avec leur source

Tous mesurés le 2026-09-08 ou le 2026-09-09. Les chiffres de backtest
vieillissent à chaque retrain ; `gate.json` et `docs/plan-dev-modele.md` font
foi, jamais ce fichier.

| Chiffre du post | Valeur exacte | Source |
|---|---|---|
| « deux hivers du backtest » | rolling-origin 4×90 j sur ~914 jours pour les stations vent ; holdout 365 j pour la marée | `docs/review_codex_2026-08-05.md` § 2 ; `docs/plan-dev-modele.md` § Pics |
| « bat la physique sur 8 stations sur 9 » sur le décile supérieur prévu | gain hors biais positif partout sauf anglet (−6,7 %) — diagnostic du 2026-09-08 sur `8e7230b` | `docs/plan-dev-modele.md` § Pics, tableau du diagnostic ; `docs/superpowers/specs/2026-09-08-peaks/event_diag_2026-09-08_8e7230b.json` |
| « 60 à 80 % de l'amplitude » | ratio std(correction prédite)/std(correction observée) : 62 % (pierres-noires), 71, 80, 64, 76, 73 % ; anglet 51 %, brest 98 %, saint-malo 85 % | idem |
| « pondérer les tempêtes ferait échouer mon critère » | gate médian = gain hors biais ≥ 5 % sur la fenêtre entière, inchangé | `docs/superpowers/specs/2026-09-08-peaks-design.md` § 1 ; `pipeline/scripts/train.py` `GATE` |
| Alerte : Brier skill vs climatologie, IC95 par jour d'émission, POD ≥ physique seuillée | gate `evaluation_ready AND n_events ≥ 24 AND bss > 0 AND bss_ci95_low > 0 AND (pod_baseline is None OR pod ≥ pod_baseline)` | `pipeline/scripts/train.py` `_peak_verdicts` ; spec § 2.1. Pour la marée, pas d'adversaire déterministe (l'harmonique n'a pas de surcote) |
| Borne : quantile 0,9, couverture 85–95 % | `PEAK_COVERAGE = (0.85, 0.95)`, gain pinball IC95 basse > 0 | `pipeline/scripts/train.py` ; spec § 2.2 |
| « l'alerte passe partout » (5 stations) | brest, saint-malo, ouessant, dieppe, cherbourg-vent : `peaks.alert.pass = true` — release `84a4473` du 2026-09-09 | `pipeline/models/gate.json` |
| « la borne passe à trois » | brest, saint-malo, cherbourg-vent `peaks.band.pass = true` ; ouessant, dieppe `false` | idem |
| « Ouessant 84,9 %, Dieppe 84,0 % » | coverage 0,849 et 0,840 | idem ; mesure `baaddd4` dans `docs/plan-dev-modele.md` § Pics |
| Saint-Malo : alerte PASS, POD < 0,5, FAR ≈ POD | BSS 0,231 [0,163 ; 0,295], POD 0,467, FAR 0,468 | idem |
| « quatre stations de houle sans verdict » | pierres-noires, belle-ile, anglet, cherbourg : `evaluation_ready = false`, pas d'entrée `peaks` dans `gate.json` | `pipeline/models/gate.json` ; `docs/review_codex_2026-08-05.md` § 2 |
| Page « extrêmes » sélectionnée au maximum observé | `compute_extreme_episodes` trie sur `obs_peak` ; réserve ajoutée à la docstring | `pipeline/src/scoreboard/publish.py` |
| « Dieppe, 8 m/s » | `threshold_p90` = 8,0 m/s dans `data/dieppe/peaks.json` du 2026-09-09 ; `p_48h` = 0,70 ce jour-là | `data/dieppe/peaks.json` (commit `55e1dcb`) |
| Seuil affiché à côté du pourcentage | `PeaksPanel.jsx`, `ScoreboardTable.jsx` (badge avec tooltip « 70 % — vent > 8.00 m/s ») | repo site, commit `30b6924` |

**Attention à l'homonymie** : `cherbourg` (houle, sans verdict) et
`cherbourg-vent` (vent, alerte et borne PASS). Le post ne nomme aucun des
deux.

---

## Ce qui a été délibérément laissé de côté

- **Tout chiffre de performance publiée des sorties pics.** Le tableau
  « Détection des dépassements » du site est vide le 2026-09-09 : aucune
  veille scorée. Il n'y aura un chiffre citable qu'après 30 jours, et la
  réserve backfill (`docs/plan-dev-modele.md` § Réserves ouvertes) devra
  être levée avant de le citer.
- **Le diagnostic 48 h (`p48_bss_clim`)** : référence prise côté test, donc
  optimiste, et non gaté. Pas dans un post.
- **Le p98** : mesuré, non gaté, non publié.
- **Les deux décisions d'écart à la spec** (pas d'indépendance vis-à-vis du
  médian en production, pas d'adversaire déterministe pour l'alerte marée) :
  matière technique, documentées dans la spec, pas pour LinkedIn.
- **Les 70 % de Dieppe** du jour : une valeur d'un jour n'est pas un
  résultat. Le post cite le seuil, pas la probabilité.
