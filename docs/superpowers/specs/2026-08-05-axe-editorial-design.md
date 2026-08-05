# Axe éditorial du scoreboard — design

**Date** : 2026-08-05
**Portée** : AXE 3 du `plan_dev_gemini.md` (diffusion, inbound), volets
« baromètre / posts LinkedIn » et « widget Verified by ».
**État du code au moment de la rédaction** : commit `d9dd65a` (scoreboard),
`adb3459` (site).

## Objectif

Faire arriver des **missions de conseil entrantes**. Le succès se mesure en
demandes de mission ODC déclenchées par une lecture, pas en vues ni en abonnés.

Le scoreboard est la démo publique et permanente du **Service 01 —
Modélisation & recalage** vendu sur le site : « recalage statistique de sorties
WW3, CROCO, CMEMS ou GFS contre bouées et marégraphes », livrable « rapport de
validation : RMSE, biais, scatter index ». L'axe éditorial n'a donc rien à
inventer : il donne à voir un service déjà vendu, tournant en continu sur
9 stations.

## Positionnement

Deux règles, non négociables, qui s'appliquent à tout post de cet axe :

1. **Post-traitement, jamais concurrence.** L'IA ne remplace pas le modèle
   physique : elle le corrige localement, et sans lui elle n'a rien à corriger.
   Les modèles (MFWAM, ARPEGE, ECMWF, GFS-Wave, EWAM) sont nommés comme
   références techniques, jamais comme adversaires battus. Météo-France est un
   partenaire ou client possible dans cette niche, et un post perçu comme un
   tacle y circule vite.
2. **Le sujet est la mesure, pas le modèle.** Ce qui est raconté, c'est qu'on
   évalue honnêtement et en continu — pas qu'on gagne.

## Cadence

**Au fil des résultats, sans calendrier.** Le risque assumé de ce choix est
qu'il ne reparte jamais après le premier post. Il est traité non par un
calendrier mais par une liste de déclencheurs (ci-dessous) : quand l'un d'eux
se produit, il y a matière ; sinon, il n'y a rien à dire et on ne dit rien.

## Post n°1

### Angle

**« Combien vaut vraiment un post-traitement IA ? »** — la réponse mesurée le
2026-08-05, qui est contre-intuitive : selon la station, une régression
linéaire régularisée fait aussi bien qu'un gradient boosting.

Trois raisons de commencer par celui-là :

- il est frais, mesuré le jour même ;
- il est anti-hype dans un marché saturé de promesses IA — ce qui démontre du
  **jugement** plutôt que de la technique, et c'est le jugement qu'on achète
  chez un consultant ;
- il ne dépend pas des jours opérationnels qui manquent encore, puisqu'il porte
  sur des backtests rolling-origin et non sur la performance publiée.

### Cadrage obligatoire : mesuré ≠ servi

Les modèles servis en production et ceux que la mesure du 2026-08-05
retiendrait **diffèrent**, parce que la mesure a tourné sur les datasets houle
régénérés le jour même (après correction du plafond Candhis) alors que les
artefacts déployés ont été entraînés sur les anciens :

| station | modèle servi (artefact) | retenu par la mesure du jour |
|---|---|---|
| pierres-noires | `hgb-per-lead` | `hgb` |
| belle-ile | `hgb-per-lead` | `ridge` |
| cherbourg | `hgb` | `ridge` |
| anglet | `ridge` | `ridge` |

**Le post parle donc de la mesure, jamais de la production.** Formulation
imposée : « je viens de mesurer », pas « mes stations tournent en ». Un post
qui annoncerait la seconde chose serait démenti par `stations.json`, qui publie
`model_name` par station depuis le commit `1d8a489`.

Décision explicite : **on ne réentraîne pas** pour faire coïncider les deux.
Engager une promotion de modèles pour les besoins d'un post serait laisser la
communication décider de la production.

### Structure

1. **Accroche** — le résultat contre-intuitif en deux lignes. Aucun chiffre de
   performance, aucune formule « l'IA révolutionne ».
2. **Le protocole**, court mais présent : rolling-origin sur 4 origines, purge
   de 48 h, et surtout **bootstrap apparié sur les mêmes jours d'émission**. Le
   point méthodologique qui vaut d'être dit : deux intervalles de confiance qui
   se chevauchent ne concluent rien ; seul l'écart mesuré sur les mêmes heures
   conclut. C'est ce qui sépare le post d'une opinion.
3. **Le résultat**, sans arrondir dans le bon sens : sur les 5 stations de
   marée et de vent, le boosting est payé — borne basse de l'IC95 strictement
   positive partout, de +2,1 à +12,9 points. Sur les 4 stations de houle, une
   seule (pierres-noires, +2,1 pt, IC95 [+1,3 ; +3,1]). Sur deux autres, la
   sélection automatique retient d'elle-même la régression linéaire.
4. **La station qu'on ne sait pas trancher** : anglet, une seule origine
   exploitable à cause d'un trou de bouée de 7 mois. Indéterminé, pas nul. Il
   faut ~9 mois de mesure, pas un ajustement de protocole — et on refuse de
   baisser le seuil de validation pour repêcher un chiffre.
5. **La preuve de crédibilité** : une station de houle est publiée sur le site
   *comme non publiée* — elle rate le gate à +3,0 % de gain débiaisé, sous le
   seuil de 5 %. Publier ses refus est ce qui rend les autres chiffres
   lisibles.
6. **Le positionnement**, une phrase (règle 1 ci-dessus).
7. **Sortie douce** : le scoreboard est public, la page méthode explique tout,
   le CSV est téléchargeable. Le lien pointe vers une **page station**, pas la
   home.

### Contraintes de forme

- ~1500 à 1800 signes, un seul lien, pas d'emoji en tête de ligne, pas de
  grappe de hashtags.
- Première personne, factuel, aucune promesse.
- Pas de « contactez-moi ». Le Service 01 n'est pas nommé : la page qu'on
  atteint le dit déjà.

### Interdits

- **Aucun chiffre de performance publiée.** 30 à 31 des 32 jours notés sont
  reconstitués : ce sont des bornes hautes, pas des performances terrain. Voir
  `docs/biais-forcage-jours-reconstitues.html`.
- Aucune comparaison frontale à Météo-France.
- Aucune mention des offres B2B de l'AXE 4.

## Déclencheurs des posts suivants

Tous observables dans des fichiers que le pipeline publie déjà. Aucun n'est
automatisé (voir « Hors périmètre »).

1. **Premier mois opérationnel** — quand `n_days_backfilled / n_days` passe
   sous 50 % sur une station, le post de performance devient honnête. C'est le
   déclencheur majeur, à ~1 mois, et il débloque l'angle « fenêtres de
   transfert CTV en éolien offshore » sur cherbourg-vent (MAE 1,48 → 1,12 m/s).
2. **Événement extrême** où l'écart IA/physique est large — `extremes.json`.
3. **Réserve fermée ou résultat négatif** — le plafond saint-malo du
   2026-08-05 en est un.
4. **Panne trouvée et corrigée** — 29 jours de scores fondus sans trace, une
   archive qui prétend servir 5 ans et en sert 1, une asymétrie de protocole
   qui rendait une mesure impossible. Angle crédible auprès d'un acheteur
   technique ; à réserver à celui-là.
5. **Station qui bascule** publiée ↔ non publiée : le gate qui change d'avis
   en public.

## Widget « Verified by » — correction d'une prémisse fausse

Le plan présente le widget comme un générateur de **backlinks SEO à haute
autorité**. Cette prémisse ne tient pas en l'état.

Constat (vérifié le 2026-08-05 dans `ScoreboardWidgetPage.jsx`) : le widget
porte bien un lien (`href={homeHref}`), mais **à l'intérieur de l'iframe**.
L'iframe appartient au domaine `oceandataconsulting.fr` — pour un moteur de
recherche, c'est un lien du site vers lui-même. Le tiers qui intègre le badge
ne crée aucun backlink. Chaque intégration est donc du travail offert sans
retour.

**Correction** : le snippet fourni sur la page méthode doit contenir un
`<a href>` visible **hors** de l'iframe, dans le HTML de la page hôte — une
ligne d'attribution sous le badge. C'est une modification de texte, pas de
code.

La prospection d'intégrateurs (capitaineries, portails météo, clubs nautiques)
reste **hors périmètre** : c'est du démarchage, pas de l'éditorial.

## Hors périmètre (YAGNI explicite)

- Aucun système d'alerte ni détection automatique de déclencheur.
- Aucune capture d'email, aucun gating de l'export CSV (l'AXE 3 le prévoit,
  Matthieu l'a explicitement repoussé en dernière priorité le 2026-08-05).
- Aucun calendrier éditorial, aucun gabarit de post réutilisable.

Motif commun : construire une mécanique éditoriale avant d'avoir la preuve
qu'un seul post intéresse quelqu'un serait l'échafaudage « pour plus tard » que
ce projet refuse partout ailleurs. On regarde ce que produit le post n°1, et on
décide de la suite sur des données.

## Livrables

1. Le texte du post n°1, prêt à publier — **rien n'est envoyé sans relecture**.
2. La ligne d'attribution hors iframe dans le snippet de la page méthode
   (repo site).
3. Les cinq déclencheurs inscrits dans l'item AXE 3 du `dev-dashboard.html`.
