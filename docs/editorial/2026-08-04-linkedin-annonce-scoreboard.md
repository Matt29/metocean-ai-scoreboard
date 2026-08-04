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
(brest +53,1 → +53,3 %, saint-malo +30,0 → +28,0 %). Le post peut donc citer
des chiffres. Détail et méthode : `docs/plan-dev-modele.md`.

État au 2026-08-04, recopié de `gate.json` : **8 stations notées sur 9**, une
seule sous le gate (cherbourg, houle). Brest ET Saint-Malo passent, sur le gain
hors biais.

⚠️ **La distinction qui reste, et qui ne doit jamais sauter** : les chiffres
ci-dessous sont des chiffres de **backtest**, mesurés sur une année de test
tenue à l'écart de l'entraînement. Ce ne sont pas des jours notés en direct — il
n'y en a encore quasiment aucun. Le post doit le dire, en une phrase, sans s'en
excuser.

---

## Post 1 — l'annonce (à publier maintenant)

J'ai mis en ligne un scoreboard qui note en public une IA de prévision
météo-océanique. Surcote, houle et vent, sur 9 stations des côtes françaises.
Tous les jours, y compris les jours où elle perd.

**Le principe.** Le modèle ne remplace pas la prévision physique : il la
post-traite. Il apprend l'erreur résiduelle du modèle officiel à une station
donnée, et la corrige. Chaque jour, la correction publiée la veille est
confrontée à l'observation réelle — bouée Candhis, marégraphe SHOM,
anémomètre Météo-France.

**Contre quoi ?** C'est la question qui décide si un chiffre veut dire quelque
chose. Beaucoup d'annonces comparent une IA à « la physique », sans dire
laquelle. Ici la baseline est nommée station par station, et ce n'est pas un
modèle fixe : à l'entraînement, chaque station retient le **meilleur** des 5
modèles de vagues — ou des 3 modèles de vent — disponibles à sa position. Les
Pierres Noires sont comparées à NCEP GFS-Wave, Belle-Île à EWAM, Ouessant à
ARPEGE. Battre le meilleur concurrent disponible, pas un épouvantail.

**Sur les marégraphes, ce que l'IA prédit, c'est la surcote.** La baseline y
est une analyse harmonique de la marée : l'astre seul, sans météo. Une
harmonique ne se trompe jamais sur la lune et ne voit jamais une dépression.
Tout ce qui reste entre elle et le marégraphe — la surcote — est précisément ce
que le vent et la pression ajoutent. C'est ça que le modèle apprend.

Sur l'année de test tenue à l'écart, à Brest : **11,9 cm d'erreur moyenne pour
l'harmonique, 5,6 cm pour le modèle.** L'erreur est divisée par deux. À
Saint-Malo, 15,5 → 11,2 cm.

Trois choses rendent ce chiffre défendable, et c'est le vrai sujet du post :

**1. Il tient à l'échéance.** +63 % à 1-6 h, encore **+50 % à 37-48 h**. Un
modèle qui ne gagnerait qu'à très courte échéance ferait du nowcasting déguisé :
il lirait l'erreur d'hier et la prolongerait. Ici la marge survit à deux jours.

**2. C'est bien de la physique, pas un recalage.** Si je coupe le vent et la
pression et que je ne laisse que la mémoire de l'erreur passée, le gain tombe de
53 à 39 %. Ces 14 points sont la part que seule la météo peut expliquer.

**3. Il est mesuré contre une baseline déjà débiaisée.** Le gate d'entrée du
scoreboard ne compare pas à l'harmonique brute, mais à l'harmonique dont on a
retiré son biais moyen — pour qu'un simple recalage de moyenne ne puisse jamais
passer pour de la prévision.

Et c'est là où ça devient contre-intuitif : **j'ai passé une partie de la
journée à rendre mon concurrent meilleur.** L'analyse harmonique était calée sur
90 jours. Beaucoup trop court : certaines composantes de la marée ne se séparent
qu'au-delà de six mois, et la composante annuelle demande une année pleine. Un
an tombe *pile* sur le seuil théorique de séparabilité, ce qui est la définition
d'une marge nulle. Je suis passé à deux ans. L'erreur de la baseline physique a
chuté de 29 %.

Améliorer le concurrent n'a pas réduit ma marge — ça a supprimé son avantage
injuste. Tant que la baseline dérivait, la version « débiaisée » à laquelle on
la compare avait le droit de corriger gratuitement plusieurs centimètres. Contre
une baseline propre, cet avantage disparaît, et ce qui reste est du savoir-faire
ou rien. Brest et Saint-Malo, qui échouaient au critère dur, le passent
maintenant.

**Une réserve, écrite plutôt que cachée.** Ces chiffres sont du backtest, sur
une année tenue à l'écart de l'entraînement. Ce ne sont pas encore des jours
notés en direct — le compteur public vient de repartir de zéro, et c'est voulu :
les stations de vent viennent d'émettre leur première prévision, les stations de
houle repartent de 1 jour noté. Rien n'est maquillé derrière un historique
reconstitué. Les jours seront publiés au fur et à mesure, y compris les mauvais.

Une station de houle, Cherbourg, reste sous le gate. Elle reste au tableau avec
son verdict écrit, plutôt que retirée de la liste. C'est le genre de ligne qu'on
ne trouve jamais dans une annonce, et c'est précisément pour ça qu'elle y est.

Code, données quotidiennes et verdicts des stations qui échouent : tout est
ouvert.

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
   dessous, la surcote seule. Sur ces 48 h, 46 cm d'erreur pour l'harmonique,
   11 cm pour le modèle. C'est le visuel qui montre en une seconde ce qu'aucune
   phrase ne fait passer : l'harmonique est *décalée vers le bas pendant deux
   jours*, et c'est cet écart-là que le modèle prédit.

   Deux honnêtetés à assumer si on en parle en commentaire : le modèle
   sous-estime le pic (58 cm prédits contre 85 observés), et cette émission est
   la plus forte surcote de l'année de test — c'est un cas de tempête, pas une
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
