# Post n°2 — offre « Private Scoreboard » sur données client — design

**Date** : 2026-08-06
**Portée** : AXE 4, Offre 1 du `plan_dev_gemini.md`, portée par un post de
l'AXE 3. Étend
[`2026-08-05-axe-editorial-design.md`](2026-08-05-axe-editorial-design.md) —
qui reste la référence de l'axe — d'un **second genre de post**.
**État au moment de la rédaction** : commit `5d277e2`. Post n°1 publié le
2026-08-06.
**Statut** : **cahier des charges seul. Le texte n'est pas écrit et ne peut pas
l'être aujourd'hui** — il s'adosse à une mesure qui n'existera qu'au
déclencheur (voir « Calendrier »).

## Objectif

Le même que l'axe : des missions de conseil entrantes. Ce post-ci vise
explicitement l'**Offre 1 — Private Scoreboard** : *« intégration de capteurs
privés et calibration sur-mesure d'un dashboard de suivi de performance »*,
cible parcs éoliens offshore, ports, chantiers navals.

Il est vendu comme **mission de conseil sur-mesure**, jamais comme produit :
l'offre est planifiée Q1/Q2 2027 et n'a aucun avancement rapporté. Rien dans le
dépôt ne sert aujourd'hui un client privé.

## Un second genre, et pourquoi il fallait le cadrer

Le cahier des charges du post n°1 porte un interdit explicite : *« aucune
mention des offres B2B de l'AXE 4 »*. Cet interdit était juste **pour ce
post-là** — sa force venait de ne rien vendre. Il ne dit pas que l'axe s'interdit
à jamais de parler d'offre ; il dit qu'un post de mesure et un post d'offre sont
deux choses, comme l'annonce de l'outil et le premier résultat en avaient été
deux le 2026-08-04.

D'où ce fichier. Les deux règles de positionnement de l'axe **restent
intégralement applicables** : post-traitement jamais concurrence, et le sujet
reste la mesure — ici, ce qu'on saurait mesurer chez le client, pas ce qu'on
lui promet.

## Calendrier — adossé au déclencheur n°1, pas au calendrier

Sortie prévue au **déclencheur n°1** de l'axe : premier mois opérationnel,
`n_days_backfilled / n_days` sous 50 % sur une station. Attendu vers le
2026-09-05, à vérifier dans `data/scores.json`, jamais supposé.

Trois raisons, dans l'ordre de force :

1. Ce déclencheur débloque l'angle « fenêtres de transfert CTV en éolien
   offshore » sur `cherbourg-vent` — **exactement la cible de l'Offre 1**. Le
   post d'offre s'adosse alors à un résultat mesuré en direct au lieu d'être
   une promesse nue.
2. Le post n°1 tire sa crédibilité de ne rien vendre. Enchaîner sous 48 h le
   ferait relire rétroactivement comme une accroche commerciale.
3. La cadence de l'axe est « au fil des résultats ». Un post d'offre qui ne
   suit aucun résultat est précisément le calendrier déguisé que l'axe refuse.

**Si le déclencheur ne tombe pas, le post ne sort pas.** Pas de repli sur une
date.

## Angle

**« Ce que vos données permettent — et à partir de quand. »**

Le post ne dit pas « je peux faire pareil chez vous ». Il dit ce qu'on saurait
conclure d'un capteur privé (bouée houle, ADCP) près d'un site donné, **et à
quelle profondeur d'historique chaque conclusion devient possible**.

### Cadrage obligatoire : le prospect type, c'est Anglet

C'est le cœur du post et sa principale valeur.

Quelqu'un qui vient d'installer un ADCP près de son site n'a pas d'historique.
Le protocole du scoreboard donne alors exactement ce qu'il a donné à Anglet :
une seule origine exploitable, un IC95 % de largeur nulle, **indéterminé** — et
il a été explicitement refusé de baisser `VAL_DAYS_CAP` pour repêcher un
chiffre. Donc « je peux faire pareil chez vous » est **faux pour la majorité des
lecteurs**, et le post doit le dire avant qu'eux ne le découvrent.

Ordres de grandeur à citer, avec leur source, **à revérifier avant envoi** :

| Ce qui devient possible | Profondeur d'historique | Source |
|---|---|---|
| Une première lecture, une origine | ~90 j de train par origine | `VAL_DAYS_CAP`, protocole rolling-origin |
| Le protocole complet, 4 origines, houle | ~730 j | dashboard § reste à faire ; wind déjà à ~914 j |
| Sortir Anglet de l'indéterminé | ~9 mois de plus | `plan-dev-modele.md` § houle |

Dire cela vend **le jugement**, qui est ce qu'on achète chez un consultant, et
distingue d'un fournisseur qui répond oui à tout. C'est aussi ce qui rend
crédible la suite : quelqu'un qui annonce ce qu'il ne peut pas conclure est
cru quand il annonce ce qu'il conclut.

### L'alerte : à construire, jamais livrée

`extremes.json` recense des épisodes extrêmes **a posteriori** sur l'historique
publié. Il n'y a **ni seuil client, ni franchissement en prévision, ni
notification** — rien de ce qu'un port ou un parc appelle « alerte ».

Formulation imposée : « un système de seuils et d'alertes **se construit avec
vous** ». Aucune capture, aucune démo, aucun conditionnel qui laisse croire que
la brique existe. Annoncer une alerte inexistante dans un post dont l'argument
est l'honnêteté détruirait les deux.

## Structure visée

1. Le résultat frais du déclencheur — un mois de scores en direct, plus de
   moitié non reconstitués, sur `cherbourg-vent`. **Chiffres à remplir le jour
   même.**
2. Ce que la même méthode donnerait sur un capteur privé près d'un site.
3. La condition, en toutes lettres : la profondeur d'historique. Le cas Anglet
   comme contre-exemple assumé.
4. Ce qui est refusé : aucun gain annoncé avant mesure. Le gate qui publie ses
   refus — Cherbourg reste au tableau sous le seuil — est la preuve que le
   refus n'est pas rhétorique.
5. Seuils et alertes : se construisent, ne sont pas livrés.
6. Un lien.

## Contraintes de forme

- Héritées de l'axe : ~1500 à 1800 signes, un seul lien, pas d'emoji en tête de
  ligne, pas de grappe de hashtags, première personne, factuel.
- **Pas de « contactez-moi »**, comme pour le post n°1. La page atteinte fait ce
  travail.
- Différence assumée avec le post n°1 : le lien pointe vers la page **Service
  01** du site ODC, pas vers une page station. **URL à vérifier dans le repo
  site avant envoi** — elle n'est pas connue à la rédaction de ce cahier des
  charges et ne doit pas être devinée.

## Interdits

- **Aucun chiffre de performance publiée issu de jours reconstitués.** Au
  déclencheur, une partie des jours cesse de l'être : citer alors uniquement la
  fenêtre non reconstituée, avec `n_days` et `n_days_backfilled` à l'appui.
- Aucune comparaison frontale à Météo-France (règle 1 de l'axe).
- Aucun nom de client, prospect ou site tiers.
- Aucune capture d'un dashboard privé — il n'en existe aucun.
- Aucun prix, aucun délai de livraison, aucun engagement de performance.

## À remplir le jour du déclencheur

- Les chiffres du premier mois opérationnel de `cherbourg-vent`, relevés dans
  `data/scores.json`, avec leur date et le commit.
- Vérification que `n_days_backfilled / n_days` est bien sous 50 % — c'est la
  condition, pas une formalité.
- L'URL de la page Service 01.
- Une relecture du tableau des ordres de grandeur ci-dessus contre l'état du
  code à cette date.

## Hors périmètre (YAGNI explicite)

- Automatiser la détection du déclencheur. Comme pour l'axe : cinq déclencheurs,
  aucun automatisé, délibérément.
- Construire quoi que ce soit de l'Offre 1 avant qu'une mission ne la finance.
- Une page d'atterrissage dédiée, un formulaire, une capture de leads — l'AXE 3
  a déjà un item pour ça, non commencé.

## Livrables

1. Ce fichier.
2. Au déclencheur : `docs/editorial/<date>-linkedin-post-offre-private-scoreboard.md`,
   même format que le post n°1 — texte, tableau des chiffres avec leur source,
   et liste de ce qui a été laissé de côté.
