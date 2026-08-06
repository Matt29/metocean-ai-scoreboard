# Post LinkedIn — « combien vaut vraiment un post-traitement IA ? »

**Date** : 2026-08-05
**Statut** : **brouillon, non publié** — rien n'est envoyé sans relecture.
**Angle** : la mesure du 2026-08-05, contre-intuitive — selon la station, une
régression linéaire régularisée fait aussi bien qu'un gradient boosting. Le
sujet est la mesure, pas le modèle, et jamais la production.
**Cahier des charges** :
[`docs/superpowers/specs/2026-08-05-axe-editorial-design.md`](../superpowers/specs/2026-08-05-axe-editorial-design.md)
(post n°1).

---

## Texte du post — prêt à copier-coller

<!-- DÉBUT DU POST -->

Je viens de mesurer ce que vaut mon post-traitement IA face à une régression linéaire régularisée. Sur la houle, aux deux tiers des stations mesurables, la régression fait aussi bien.

Protocole : rolling-origin sur 4 origines, purge de 48 h, bootstrap apparié sur les mêmes jours d'émission.

C'est le cœur. Deux intervalles de confiance qui se chevauchent ne concluent rien : chacun mesure sa propre incertitude, pas l'écart entre les deux. Seul cet écart, mesuré jour d'émission par jour d'émission avec sa propre barre d'erreur, conclut. Beaucoup de comparaisons publient la première chose en croyant avoir montré la seconde.

Sur les 5 stations de marée et de vent, le boosting est payé : borne basse de l'intervalle strictement positive partout, de +2,1 à +12,9 points. Sur les 4 stations de houle, il s'inverse — une seule conclut, Les Pierres Noires, +2,1 pt, IC95 [+1,3 ; +3,1]. Sur deux autres, la sélection automatique retient d'elle-même la régression linéaire.

Une station que je ne sais pas trancher : Anglet. Un trou de bouée de sept mois ne laisse qu'une origine exploitable, et un intervalle calculé sur un seul groupe ne peut pas varier. Ce n'est pas un écart nul, c'est l'absence de mesure. Il faut neuf mois de mesure de plus, pas un ajustement de protocole : baisser le seuil de validation pour repêcher un chiffre, c'est déplacer les poteaux.

Et une station de houle est publiée sur le scoreboard comme non publiée : Cherbourg, +3,0 % de gain hors biais, sous le seuil de 5 % qui autorise la mise en ligne. Elle reste au tableau avec son verdict écrit. Publier ses refus est ce qui rend les autres chiffres lisibles.

Rien là-dedans ne dit qu'une IA remplace un modèle physique : elle post-traite MFWAM, ARPEGE ou GFS-Wave, et sans eux elle n'a rien à corriger.

Le scoreboard est public, la méthode et les données avec.

https://oceandataconsulting.fr/scoreboard/cherbourg

<!-- FIN DU POST -->

**Longueur** : 1 897 signes espaces compris (1 846 hors URL).

---

## Les chiffres employés, avec leur source

À revérifier avant envoi. Les chiffres de backtest vieillissent à chaque
retrain ; `gate.json` et `docs/plan-dev-modele.md` font foi, jamais ce fichier.

| Chiffre du post | Valeur exacte | Source |
|---|---|---|
| 5 stations marée/vent, boosting payé partout | brest, saint-malo, ouessant, dieppe, cherbourg-vent — borne IC95 % basse de l'écart strictement positive sur les 5 | `docs/plan-dev-modele.md` § « Ridge comme plancher honnête », tableau « L'écart, avec sa barre d'erreur » |
| « de +2,1 à +12,9 points » | min = brest +2,1 pt [+0,7 ; +3,5] ; max = saint-malo +12,9 pt [+10,7 ; +15,1] (ouessant +9,1 ; dieppe +5,4 ; cherbourg-vent +6,3) | idem |
| 4 stations de houle, une seule conclut | pierres-noires, belle-ile, cherbourg, anglet | `docs/plan-dev-modele.md` § « Ridge sur les 4 stations houle », tableau « L'écart » |
| Les Pierres Noires, +2,1 pt, IC95 [+1,3 ; +3,1] | Δ (`hgb` − `ridge`) = +2,1 pt, IC95 % [+1,3 ; +3,1] pt | idem |
| « sur deux autres, la sélection automatique retient la régression linéaire » | belle-ile et cherbourg : incumbent = `ridge`, Δ = +0,0 pt | idem |
| Anglet, une seule origine exploitable | `train 12484 / test 4260 rows, 1 origin(s)` — 3 origines écartées avant le test | `docs/plan-dev-modele.md`, § houle, paragraphe sous le tableau « Par candidat » |
| « neuf mois de mesure de plus » | ~9 mois pour qu'une 2ᵉ origine atteigne les 90 j de train requis ; baisser `VAL_DAYS_CAP` explicitement refusé | idem, § « L'écart », commentaire anglet |
| Cherbourg publiée comme non publiée | `cherbourg` (houle, baseline `ewam`) : `gain_debiased` = 0.0305, `pass` = false | `pipeline/models/gate.json` |
| « +3,0 % de gain hors biais » | 0.0305 → +3,0 % | idem |
| « seuil de 5 % » | `GATE = 0.05` | `pipeline/scripts/train.py:51` |
| Station affichée non publiée sur le site | `{"id": "cherbourg", "kind": "wave", "published": false}` | `data/stations.json` |
| URL de la page station | `https://oceandataconsulting.fr/scoreboard/cherbourg` | `ScoreboardStationPage.jsx:150` (repo site), route `/scoreboard/<id>` |

**Attention à l'homonymie** : `cherbourg` (houle, bouée, `ewam`) et
`cherbourg-vent` (vent, Homet, ARPEGE) sont deux stations distinctes. Le post
ne cite `cherbourg` que dans le paragraphe « publiée comme non publiée » ;
`cherbourg-vent` n'apparaît que dans la fourchette +2,1 / +12,9 pt, sans être
nommé.

---

## Ce qui a été délibérément laissé de côté

- **Tout chiffre de performance publiée.** 30 des 32 jours notés à
  pierres-noires sont reconstitués (`data/scores.json`, `n_days` = 32,
  `n_days_backfilled` = 30) : ce sont des bornes hautes, pas des performances
  terrain. Voir `docs/biais-forcage-jours-reconstitues.html`. Aucun MAE, aucun
  gain publié, aucune capture du tableau ne doit accompagner ce post.
- **La stack servie en production.** Les modèles servis et ceux que la mesure
  du 2026-08-05 retiendrait diffèrent (pierres-noires sert `hgb-per-lead`,
  belle-ile `hgb-per-lead`, cherbourg `hgb`). `stations.json` publie
  `model_name` par station : annoncer « mes stations tournent en ridge » serait
  démenti en trois clics. Le post dit « je viens de mesurer », jamais « je sers ».
  Décision associée : **on ne réentraîne pas** pour faire coïncider les deux.
- **Le fait qu'aucune des 4 stations houle n'est `evaluation_ready`** (holdout
  dégradé, 1 à 3 folds sur 4). C'est vrai et documenté, mais orthogonal à la
  question ridge/boosting : l'expliquer coûterait 300 signes et diluerait les
  §4 et §5, qui portent la crédibilité.
- **Le détail du coût** (`ridge` 0,7 s et 2 ko contre `hgb-per-lead` 10,5 s et
  3 247 ko sur brest, ×15 en temps et ×1 600 en artefact). Bon matériau de
  réponse en commentaire, pas assez central pour le corps du post.
- **Belle-Île +25,7 % et son inversion**, `saint-malo` où `ridge` ne capte que
  62 % du gain, et le bug d'asymétrie de `train.evaluate` corrigé pour rendre
  le bootstrap apparié valide. Ce dernier est un déclencheur de post à part
  entière (« panne trouvée et corrigée »), à ne pas brûler ici.
- **Toute comparaison frontale à Météo-France**, toute mention des offres B2B,
  tout « contactez-moi », toute grappe de hashtags, tout emoji.

---

## Figure associée

[`figure-ecart-boosting-ridge.png`](figure-ecart-boosting-ridge.png) — les 9
écarts Δ avec leurs IC95 %, produite le 2026-08-06 par
`pipeline/scripts/figure_ridge_deltas.py`, qui **parse** les deux tableaux
« L'écart, avec sa barre d'erreur » de `docs/plan-dev-modele.md` : aucune valeur
n'y est retapée, et un changement de format du tableau fait échouer le script
plutôt que dessiner un chiffre périmé.

Elle ne porte **aucun chiffre de performance publiée** — que des écarts de
backtest — donc aucun jour reconstitué. Anglet y est un cercle creux annoté
« absence de mesure, pas un écart nul », jamais un point à zéro. Verdicts en
libellé *et* en couleur (`docs/brand.md` : jamais la couleur seule).

Restent exclus : le tableau du site et la figure de surcote, qui affichent des
chiffres issus des jours reconstitués.
