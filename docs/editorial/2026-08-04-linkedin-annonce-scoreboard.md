# Posts LinkedIn — Scoreboard Metocean IA

Scindé en deux posts le 2026-08-04 : le brouillon initial mettait en avant un
gain (−26 % aux Pierres Noires) mesuré sur des jours rejoués depuis les
archives, pas notés en direct. Le compteur en direct vient de repartir de
zéro — houle retombée à 1 jour scoré, vent à 0 jour. Annoncer l'outil et
montrer un premier résultat **en production** sont deux posts, pas un.

Réécrit une troisième fois le 2026-08-04, en fin de journée. Les deux premières
versions s'interdisaient tout pourcentage sur la marée, parce que les chiffres
étaient mesurés avec un forçage atmosphérique quasi-analyse. **Cette réserve est
levée** : le forçage d'entraînement des stations de marée est passé à des runs
ECMWF stratifiés par âge — une ligne à +48 h est forcée par une prévision
réellement émise deux jours plus tôt — et les gains n'ont quasiment pas bougé
(brest +53,1 → +53,3 %). Le post peut donc citer des chiffres.

**Réécrit une quatrième fois le 2026-08-04**, sur un reproche de fond : le post
disait comment les chiffres avaient été obtenus, et jamais à quoi ils servent. Un
lecteur qui exploite un port ou un parc offshore n'avait aucune raison de lire
au-delà du deuxième paragraphe. Le post part maintenant de la **décision**
— une fenêtre d'accès qui s'ouvre ou se ferme sur quelques centimètres d'eau —
et la rigueur de mesure devient l'argument de confiance au lieu d'être le sujet.
Ne pas revenir en arrière là-dessus : c'est ce qui rend le reste lisible.

Chiffres publiés en fin de journée, **après** l'ajout de la phase de marée et de
la tendance de pression en features : **brest +53,9 %** (11,8 → 5,4 cm),
**saint-malo +34,1 %** (15,1 → 9,9 cm). Ce sont ceux-là qui sont dans le post.
Vérifier `gate.json` avant publication, jamais ce fichier.

État au 2026-08-04, recopié de `gate.json` : **8 stations notées sur 9**, une
seule sous le gate (cherbourg, houle). Brest ET Saint-Malo passent, sur le gain
hors biais.

⚠️ **La distinction qui reste, et qui ne doit jamais sauter** : les chiffres
ci-dessous sont des chiffres de **backtest**, mesurés sur une année de test
tenue à l'écart de l'entraînement. Ce ne sont pas des jours notés en direct — il
n'y en a encore quasiment aucun. Le post doit le dire, en une phrase, sans s'en
excuser.

⚠️ **Le site affiche des chiffres plus bas que le post, et c'est attendu.** État
au 2026-08-04, recalculé depuis `data/*/history.json` : brest **+27,0 %**
(6,4 → 4,7 cm, 31 jours), saint-malo **+11,5 %** (10,8 → 9,6 cm, 30 jours). Ces
jours ont été rejoués **avant** le modèle actuel : ils remonteront un peu au
prochain backfill, à revérifier avant publication.
L'écart avec le backtest annuel (+53,9 % / +34,1 %) est **saisonnier** :
la fenêtre notée est un mois d'été, le creux annuel de surcote, où l'harmonique
seule s'en sort déjà bien — sa MAE tombe à 6,6 cm contre 11,8 cm en moyenne
annuelle. Le post traite ce point de front plutôt que de l'éviter ; ne pas
supprimer ce paragraphe en raccourcissant.

---

## Post 1 — l'annonce (à publier maintenant)

**À Saint-Malo, la marée monte de 13 mètres. Ce qui décide d'une fenêtre
d'accès, ce sont les 40 centimètres que personne n'avait prévus.**

La marée astronomique, on la connaît des années à l'avance : c'est de la
mécanique céleste, elle ne se trompe jamais. Ce qui reste imprévisible, c'est la
**surcote** — l'eau que le vent empile et que la dépression aspire. C'est elle
qui fait qu'un navire passe ou attend une marée, qu'un chantier maritime tient
sa journée, qu'une intervention offshore part ou reste à quai.

Sur l'année de test, à Brest, la prévision physique se trompe de **11,8 cm** en
moyenne sur cette surcote. Le modèle que j'ai mis en ligne se trompe de
**5,4 cm**. L'erreur est divisée par deux. À Saint-Malo, 15,1 → 9,9 cm.

Et surtout : **la marge tient à l'échéance.** +65 % à 1-6 h, encore **+48 % à
37-48 h**. C'est ce qui sépare un gadget d'un outil d'aide à la décision : une
correction qui ne vaudrait qu'à trois heures arrive trop tard pour planifier
quoi que ce soit. À deux jours, elle entre dans un plan de charge.

**Mais un chiffre d'IA ne vaut rien sans son juge.** C'est le vrai sujet.

Tout le monde annonce une IA qui « bat la physique ». Presque personne ne dit
quelle physique, à quelle station, ni ce que ça donne les jours où elle perd.
Un exploitant qui doit décider s'il fait confiance à une prévision augmentée n'a,
aujourd'hui, aucun moyen de trancher.

Alors j'ai publié le juge en même temps que le modèle : un **scoreboard** qui
note l'IA en public, tous les jours, sur 9 stations des côtes françaises —
surcote, houle et vent. Y compris les jours où elle perd.

**Contre quoi ?** C'est la question qui décide si un chiffre veut dire quelque
chose. Ici la baseline est nommée station par station, et ce n'est pas un modèle
fixe : à l'entraînement, chaque station retient le **meilleur** des 5 modèles de
vagues — ou des 3 modèles de vent — disponibles à sa position. Les Pierres
Noires sont comparées à NCEP GFS-Wave, Belle-Île à EWAM, Ouessant à ARPEGE.
Battre le meilleur concurrent disponible, pas un épouvantail.

Trois garde-fous rendent le chiffre défendable :

**1. C'est de la physique, pas un recalage.** Si je coupe le vent et la pression
et que je ne laisse que la mémoire de l'erreur passée, le gain de Brest tombe de
54 % à 38 % — **16 points**. C'est la part que seule la météo explique.

**2. La comparaison se fait contre une baseline déjà débiaisée.** Le critère
d'entrée du scoreboard ne compare pas à la physique brute mais à la physique
dont on a retiré son biais moyen — pour qu'un simple recalage de moyenne ne
puisse jamais passer pour de la prévision.

**3. J'ai passé une partie du chantier à rendre mon concurrent meilleur.**
L'analyse harmonique de la marée était calée sur 90 jours. Beaucoup trop court :
certaines composantes ne se séparent qu'au-delà de six mois, et la composante
annuelle demande une année pleine. Je suis passé à deux ans, l'erreur de la
baseline a chuté de 29 %.

Ce dernier point est le plus contre-intuitif, et c'est celui qui compte :
améliorer le concurrent n'a pas réduit ma marge, ça a supprimé son **avantage
injuste**. Tant que la baseline dérivait, la version débiaisée à laquelle on la
compare avait le droit de corriger gratuitement plusieurs centimètres — un
adversaire artificiellement fort. Contre une baseline propre, ce qui reste est
du savoir-faire ou rien.

**Une réserve, écrite plutôt que cachée.** Ces chiffres sont du backtest, sur une
année tenue à l'écart de l'entraînement. Le compteur en direct, lui, vient de
repartir de zéro, et chaque jour noté s'y ajoutera — y compris les mauvais.

**Et si vous cliquez, le tableau affichera un chiffre plus bas que celui-ci.**
C'est normal, et c'est la deuxième chose que je voulais montrer. Le scoreboard
note les jours au fil de l'eau — donc aujourd'hui, un mois d'été. Or la surcote
est un phénomène d'hiver : Brest en porte **trois fois moins en juillet qu'en
février**. Un mois d'août contient peu de chose à prévoir, la physique seule s'y
débrouille presque aussi bien, et l'écart se referme mécaniquement. Le chiffre
annuel et le chiffre du mois disent la même vérité à deux saisons différentes.
C'est exactement pour ça qu'un scoreboard se lit sur la durée, et qu'une annonce
ponctuelle ne prouve rien.

Une station de houle, Cherbourg, reste sous le critère d'entrée. Elle reste au
tableau avec son verdict écrit, plutôt que retirée de la liste. C'est le genre de
ligne qu'on ne trouve jamais dans une annonce, et c'est précisément pour ça
qu'elle y est.

Si vous exploitez un port, un chantier maritime ou un parc offshore et que vous
vous demandez ce qu'une prévision augmentée changerait à vos fenêtres
d'exploitation : le code, les données quotidiennes et les verdicts des stations
qui échouent sont ouverts. Regardez la station la plus proche de chez vous.

🔗 [lien scoreboard] · [lien GitHub]

---

## Post 1 — version courte

Même angle et même hiérarchie que le format long, sans les démonstrations. À
utiliser si le format long ne passe pas, ou en premier jet à faire suivre du long
en commentaire. **Ce qui saute** : les trois garde-fous détaillés, l'inversion
« rendre le concurrent meilleur », l'écart saisonnier avec les chiffres du site —
tous dans le post long, et tous utilisables en réponse à un commentaire.
**Ce qui ne se coupe pas, quelle que soit la longueur** : la réserve backtest et
la station sous le gate. Un post court qui les perdrait ne serait pas une version
courte, ce serait un autre post.

---

**À Saint-Malo, la marée monte de 13 mètres. Ce qui décide d'une fenêtre d'accès,
ce sont les 40 centimètres que personne n'avait prévus.**

La marée astronomique, on la connaît des années à l'avance. Ce qui reste
imprévisible, c'est la surcote — l'eau que le vent empile et que la dépression
aspire. C'est elle qui fait qu'un navire passe ou attend une marée, qu'un
chantier maritime tient sa journée, qu'une intervention offshore part ou reste à
quai.

Sur l'année de test, à Brest : **11,8 cm d'erreur pour la prévision physique,
5,4 cm pour le modèle.** Et la marge tient à l'échéance — encore **+48 % à
48 heures**. C'est ce qui la fait entrer dans un plan de charge, au lieu d'un
tableau de bord temps réel.

Mais un chiffre d'IA ne vaut rien sans son juge. Tout le monde annonce une IA qui
bat « la physique ». Presque personne ne dit **quelle** physique, ni ce que ça
donne les jours où elle perd. Un exploitant qui doit décider s'il fait confiance
à une prévision augmentée n'a, aujourd'hui, aucun moyen de trancher.

Alors j'ai publié le juge en même temps que le modèle : un scoreboard qui note
l'IA en public, tous les jours, sur 9 stations des côtes françaises. Chaque
station est comparée au **meilleur modèle physique disponible à sa position**, et
il est nommé. Une station reste sous le critère d'entrée : elle reste au tableau
avec son verdict écrit, plutôt que retirée de la liste.

Ces chiffres sont du backtest, sur une année tenue à l'écart de l'entraînement.
Le compteur en direct vient de repartir de zéro, et chaque jour noté s'y ajoutera
— y compris les mauvais.

🔗 [lien scoreboard] · [lien GitHub]

---

## Post 2 — les premiers résultats en direct (~1 semaine plus tard)

Il y a une semaine, j'ai mis en ligne un scoreboard qui note en public une IA
de prévision météo-océanique — elle post-traite la prévision physique
officielle, station par station, contre le meilleur modèle concurrent
disponible à chaque position (pas un épouvantail générique). Le détail est
dans le post précédent.

Ce qui a changé : le compteur était reparti de zéro, les premiers jours notés
en conditions réelles sont là.

**{station}** : {N} jours scorés, **{gain hors biais}%** hors correction de
biais ({erreur baseline} → {erreur modèle}).

{Ajouter une deuxième ligne si une autre station a assez de jours scorés pour
être citée, même format.}

{Une phrase honnête sur ce que ça dit à ce stade — ex : trop tôt pour
généraliser, mais le signe va dans le sens attendu / la station X reste sous
le gate, voir son verdict.}

{Si les jours en direct confirment le backtest annoncé dans le post 1, le dire
explicitement — c'est la seule façon de rendre le post 1 vérifiable après coup.
S'ils le contredisent, le dire aussi : c'est tout l'intérêt d'avoir annoncé le
chiffre avant.}

Le hors-biais reste le chiffre qui compte : un simple recalage de moyenne
suffit à gonfler un gain brut, ce serait de la décoration, pas de la
prévision.

Code, données quotidiennes et verdicts des stations qui échouent : tout est
ouvert.

🔗 [lien scoreboard] · [lien GitHub]

---

## Figure associée

**Post 1 — carrousel de 2 images.** Une seule ne peut pas porter les deux
affirmations du post (le réseau publié, et ce que le modèle prédit vraiment).

1. **`figure-brest-surcote.png`** (dans ce dossier, généré depuis les données du
   dépôt) — **l'image de tête**. Une seule émission, celle du 22 janvier 2026 à
   06 UTC, et ses 48 h : niveau observé, prédiction harmonique, modèle IA ; en
   dessous, la surcote seule. Sur ces 48 h, **44 cm d'erreur pour la physique
   seule, 10 cm pour le modèle**. Régénérée le 2026-08-04 sur le modèle publié
   par `pipeline/scripts/figure_surge.py` — **la relancer après chaque retrain**,
   la figure porte des chiffres et ce sont des affirmations comme les autres. C'est le visuel qui montre en une seconde ce qu'aucune
   phrase ne fait passer : l'harmonique est *décalée vers le bas pendant deux
   jours*, et c'est cet écart-là que le modèle prédit.

   Deux honnêtetés à assumer si on en parle en commentaire : le modèle
   sous-estime le pic (**60 cm prédits contre 83 observés**), et cette émission
   est la plus forte surcote de l'année de test — c'est un cas de tempête, pas une
   journée moyenne. Choisie sur l'observation, jamais sur la performance du
   modèle. Le dire vaut mieux que se le faire dire.

2. **Le tableau du site, recadré sur 4-5 lignes** (`ScoreboardTable`) — la
   preuve. C'est la seule image qui montre que chaque station est comparée à un
   modèle *différent* et nommé (NCEP GFS-Wave, EWAM, ARPEGE), et qui affiche le
   verdict du gate sur Cherbourg. C'est ce que personne d'autre ne publie.

La carte du réseau (`ScoreboardMap`) passe en troisième si tu fais trois images.
Une carte de France avec des points ressemble à toutes les cartes de France avec
des points ; la figure de surcote et le tableau sont les deux seuls contenus
réellement distinctifs.

**Post 2 — graphique de la station citée** (`StationChart` ou équivalent),
recadré sur la fenêtre de jours réellement scorés en direct — ne pas laisser
apparaître de jours rejoués depuis les archives dans le cadrage.

**Trois contraintes avant de capturer (valables pour les deux posts) :**

- **Attendre le cron du jour et le déploiement** avant toute capture, pour
  que `stations.json` reflète l'état décrit dans le texte.
- **Jamais avec `?fixture=1`.** La fixture est réservée au dev et sert des
  données inventées — publier une capture de fixture serait exactement la
  faute que le post reproche aux autres.
- **Pour le post 2, ne citer une station que si elle a réellement assez de
  jours scorés en direct** pour que le chiffre soit lisible — pas de jour
  unique présenté comme une tendance.

## Notes de rédaction

- **Chiffre de tête = gain hors biais**, jamais le gain affiché brut. Vrai
  pour les deux posts.
- **Backtest ≠ production, et la phrase qui le dit ne se coupe pas.** Le post 1
  cite maintenant des pourcentages — il ne l'a pas toujours fait, voir l'en-tête
  de ce fichier. Ce qui a rendu ça publiable, c'est que le forçage
  d'entraînement est devenu une vraie prévision vieillie ; ce qui reste vrai,
  c'est que ce sont des chiffres de backtest sur une année tenue à l'écart. Si
  un jour on raccourcit le post, c'est le paragraphe « une réserve, écrite
  plutôt que cachée » qui doit survivre en dernier, pas les pourcentages.
- **Ne jamais écrire « vent au parc ».** Ce sont des anémomètres côtiers à
  10 m au-dessus du sol, un proxy du vent au large. Dieppe est à 8,6 km du
  parc EMR mais à 40 m d'altitude et bien plus abritée (4,61 m/s de moyenne
  contre 7,75 à Ouessant). À surveiller particulièrement si Dieppe est la
  station citée dans le post 2.
- **Ne jamais écrire que la pression « n'a pas marché ».** Elle a fait gagner
  17 points sur Brest. Ce qui a échoué à un moment, c'est le modèle face à une
  baseline encore mal conditionnée — deux choses différentes.
- **L'accroche doit rester une décision, pas une méthode.** Le post a existé
  trois versions durant comme un exposé de méthodologie honnête ; c'était sa
  force et son plafond. La méthode n'a pas disparu, elle a changé de rôle : elle
  répond maintenant à « pourquoi vous croire », après que le lecteur a compris
  « pourquoi ça le concerne ». Si une relecture future veut raccourcir, couper
  dans les garde-fous 2 et 3, jamais dans les trois premiers paragraphes.
- **Le gain à 48 h est un argument commercial, pas une coquetterie.** +65 % à
  1-6 h et +48 % à 37-48 h : c'est ce qui fait entrer la correction dans un plan
  de charge au lieu d'un tableau de bord temps réel. Chiffres mesurés le
  2026-08-04 sur le modèle publié.
- **Les chiffres de ce fichier vieillissent à chaque retrain.** Ils ont déjà
  bougé trois fois le 2026-08-04 (forçage ECMWF, constantes harmoniques
  persistées, puis phase de marée + tendance de pression). `gate.json` et
  `docs/model-eval.md` font foi — relire les deux avant de publier, et corriger
  le post plutôt que l'inverse.
- **Saint-Malo passe désormais le gate.** L'ancienne note « ne pas arrondir, elle
  échoue à 0,05 point » est périmée : c'était vrai à la fenêtre harmonique de
  365 j. Vérifier `gate.json` avant de publier, jamais ce fichier.
- **Résister à la tentation de raconter l'inversion comme une prouesse.** Le
  fait intéressant n'est pas « j'ai réussi », c'est le mécanisme : améliorer le
  concurrent a supprimé son avantage injuste. Si ce paragraphe devient un
  humblebrag, il perd exactement ce qui le distingue.
- **Le post 2 ne doit inventer aucun chiffre.** Les accolades `{...}` sont à
  remplir depuis les données réelles au moment de la publication, pas avant.
- Angles restants pour des posts ultérieurs : le détail de la méthode du gate
  qualité ; et le fait que vieillir le forçage de 48 h n'a quasiment rien coûté
  au modèle de surcote, ce qui est un résultat en soi.
