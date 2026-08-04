# Posts LinkedIn — Scoreboard Metocean IA

Scindé en deux posts le 2026-08-04 : le brouillon initial mettait en avant un
gain (−26 % aux Pierres Noires) mesuré sur des jours rejoués depuis les
archives, pas notés en direct. Le compteur en direct vient de repartir de
zéro — houle retombée à 1 jour scoré, vent à 0 jour. Annoncer l'outil et
montrer un premier résultat réel sont maintenant deux posts, pas un.

Mis à jour deux fois le 2026-08-04, et la seconde a inversé la première —
gardé ici parce que c'est instructif. Baseline harmonique portée de 90 j à
365 j : Brest et Saint-Malo tombent sous le gate, plus aucune station de niveau
d'eau publiée. Puis portée à 730 j (365 j tombe pile sur le seuil de Rayleigh
de Sa) : **Brest repasse, sur son mérite**. Le post final raconte l'arc entier,
qui vaut mieux que chacune de ses moitiés.

État final au 2026-08-04, recopié de `gate.json` : **8 stations notées**, une
seule sous le gate (cherbourg, houle). Brest ET Saint-Malo passent, sur le gain
hors biais.

⚠️ **Les chiffres `tide` de `gate.json` ne vont pas dans un post.** Ils sont
mesurés avec un forçage atmosphérique quasi-analyse (l'API Historical Forecast
concatène les runs les plus frais ; corrélation 0,9997 avec ERA5). Ils
surestiment ce qu'une vraie échéance à +48 h délivre, d'autant plus que la
baseline astronomique, elle, ne se dégrade pas avec le lead. Le post décrit donc
le *mécanisme* et le *critère*, jamais le pourcentage. Voir
`docs/plan-dev-modele.md`.

---

## Post 1 — l'annonce (à publier maintenant)

J'ai mis en ligne un scoreboard qui note en public une IA de prévision
météo-océanique. Tous les jours, y compris les jours où elle perd.

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

**Ce qui est publié.** 9 stations sur les côtes françaises — houle, niveau
d'eau, et depuis aujourd'hui le vent. **8 sont notées.** La dernière est
mesurée chaque jour comme les autres, mais retenue : un gate qualité bloque
toute station où le modèle ne bat pas sa propre baseline autrement qu'en lui
retirant un biais moyen. Elle reste dans le tableau, avec son verdict
écrit — plutôt que retirée de la liste.

**Aujourd'hui j'ai passé la journée à rendre mon concurrent meilleur.** Sur les
marégraphes, la baseline est une analyse harmonique de la marée — l'astre seul,
sans météo. Ce que l'IA doit prévoir, c'est donc exactement la surcote : ce que
le vent et la pression ajoutent à la marée.

Cette analyse était calée sur 90 jours. Beaucoup trop court : certaines
composantes de la marée ne se séparent qu'au-delà de six mois, et la composante
annuelle demande une année pleine. Je suis passé à un an. Puis, en mesurant, à
deux ans — un an tombe *pile* sur le seuil théorique de séparabilité, ce qui est
la définition d'une marge nulle. Résultat : l'erreur de la baseline physique
chute de 29 %.

Entre les deux, le verdict de mon propre modèle s'est inversé. À un an, il
faisait match nul avec sa baseline — tout son gain apparent n'était que le
retrait d'un biais. À deux ans, il la bat pour de bon, sur le critère dur :
celui qui compare à une baseline **déjà débiaisée**, pour qu'un simple recalage
de moyenne ne puisse jamais passer pour de la prévision.

Je ne donne pas le chiffre ici, et c'est délibéré. Il est mesuré sur des
prévisions atmosphériques passées d'une qualité que la vraie échéance à 48 h
n'atteint pas — un détail d'API que j'ai découvert en le vérifiant. Le nombre
existe, il est dans le dépôt, avec la réserve écrite à côté. Il ira dans un post
quand des jours notés en direct l'auront confirmé.

Le mécanisme mérite d'être dit, parce qu'il est contre-intuitif : améliorer le
concurrent n'a pas réduit ma marge, ça a supprimé son avantage injuste. Tant
que la baseline dérivait, la version « débiaisée » à laquelle on la compare
avait le droit de corriger gratuitement 8 cm d'erreur. Baseline propre, cet
avantage disparaît, et le vrai savoir-faire du modèle devient visible.

J'ai passé la journée à muscler l'adversaire de mon produit. C'est ce qui a
fini par prouver que le produit valait quelque chose.

Une station de houle, Cherbourg, reste sous le gate. Elle reste au tableau avec
son verdict écrit, plutôt que retirée de la liste.

**Le compteur repart de zéro, et c'est voulu.** Les stations de vent viennent
d'émettre leur première prévision : elles affichent « en attente de jours
scorés ». Les stations de houle perdent leur historique rejoué et repartent
de 1 jour noté en direct. Rien n'est caché derrière un historique reconstitué
— on publie les jours notés au fur et à mesure, y compris les mauvais.

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

Le hors-biais reste le chiffre qui compte : un simple recalage de moyenne
suffit à gonfler un gain brut, ce serait de la décoration, pas de la
prévision.

Code, données quotidiennes et verdicts des stations qui échouent : tout est
ouvert.

🔗 [lien scoreboard] · [lien GitHub]

---

## Figure associée

**Post 1 — carrousel de 2 images, capture du site.** Une seule image ne peut
pas porter les deux affirmations du post (l'étendue du réseau, et la baseline
nommée station par station).

1. **La carte du réseau** (`ScoreboardMap`) — l'accroche. C'est le seul
   visuel du site qui reste lisible en vignette de fil LinkedIn, et il prouve
   la chose la plus difficile à inventer : 9 points sur de vraies côtes.
   Recadrer sur la façade Manche-Atlantique, sans le header de page.
2. **Le tableau, recadré sur 4-5 lignes** (`ScoreboardTable`) — la preuve.
   C'est la seule image qui montre que chaque station est comparée à un
   modèle *différent* et nommé (NCEP GFS-Wave, EWAM, ARPEGE), et qui affiche
   le verdict du gate sur Cherbourg. C'est ce que personne
   d'autre ne publie.

Si tu n'en veux qu'une : **le tableau**, pas la carte. Une carte de France
avec des points ressemble à toutes les cartes de France avec des points ; le
tableau est le seul contenu réellement distinctif. Il faut alors le recadrer
serré — 4 lignes maximum, lisibles sur mobile. Aujourd'hui le tableau affiche
surtout « en attente de jours scorés » : c'est cohérent avec le texte du
post, pas un problème à cacher.

**Post 2 — graphique de la station citée** (`StationChart` ou équivalent),
recadré sur la fenêtre de jours réellement scorés en direct — ne pas laisser
apparaître de jours rejoués depuis les archives dans le cadrage. C'est la
première fois que ce type de visuel est publiable : le post 1 l'interdisait
explicitement parce que les courbes disponibles au 04/08 ne portaient que de
l'historique rejoué.

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
- **Ne jamais écrire « vent au parc ».** Ce sont des anémomètres côtiers à
  10 m au-dessus du sol, un proxy du vent au large. Dieppe est à 8,6 km du
  parc EMR mais à 40 m d'altitude et bien plus abritée (4,61 m/s de moyenne
  contre 7,75 à Ouessant). À surveiller particulièrement si Dieppe est la
  station citée dans le post 2.
- **Le post 1 n'affiche aucun chiffre de performance en production** : au
  2026-08-04 il n'y a pas de jour scoré en direct à montrer. Ne pas céder à la
  tentation de citer un gain d'entraînement comme s'il valait pour la
  production — c'était l'erreur du brouillon initial. **Le +8 % de Brest et les
  −29 % de la baseline sont des chiffres d'entraînement** : ne jamais les
  présenter comme un résultat en conditions réelles. La formulation actuelle
  parle de la *baseline* et du *critère*, pas d'une performance opérationnelle
  vérifiée — c'est ce qui la rend publiable aujourd'hui.
- **Ne jamais écrire que la pression « n'a pas marché ».** Elle a fait gagner
  17 points sur Brest. Ce qui a échoué à un moment, c'est le modèle face à une
  baseline encore mal conditionnée — deux choses différentes.
- **Ne pas arrondir Saint-Malo.** Elle échoue à 0,05 point du seuil. La mention
  « à 0,05 point » est volontaire : elle montre qu'on ne bouge pas la barre pour
  faire entrer une station. C'est le même argument que le reste du post.
- **Résister à la tentation de raconter l'inversion comme une prouesse.** Le
  fait intéressant n'est pas « j'ai réussi », c'est le mécanisme : améliorer le
  concurrent a supprimé son avantage injuste. Si ce paragraphe devient un
  humblebrag, il perd exactement ce qui le distingue.
- **Le post 2 ne doit inventer aucun chiffre.** Les accolades `{...}` sont à
  remplir depuis les données réelles au moment de la publication, pas avant.
- Angle restant pour un post ultérieur : le détail de la méthode du gate
  qualité.
