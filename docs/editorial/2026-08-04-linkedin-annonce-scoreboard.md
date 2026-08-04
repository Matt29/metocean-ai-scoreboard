# Post LinkedIn — annonce du Scoreboard Metocean IA

## Version 1 — l'annonce (angles 2 + 3 fondus)

J'ai mis en ligne un scoreboard qui note une IA de prévision météo-océanique.
En public, tous les jours, y compris les jours où elle perd.

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
d'eau, et le vent depuis aujourd'hui. 7 sont notées. Les 2 autres sont
mesurées chaque jour comme les autres, mais retenues : un gate qualité bloque
toute station où le modèle ne bat pas sa propre baseline. Cherbourg est dans
ce cas depuis le dernier ré-entraînement. Elle reste dans le tableau, avec son
verdict écrit — plutôt que retirée de la liste.

**Le meilleur résultat**, aux Pierres Noires : erreur absolue de 0,213 m à
0,158 m, soit **−26 % hors correction de biais**. Hors biais, parce qu'un
simple recalage de moyenne suffit à gonfler un gain brut — et ce serait de la
décoration, pas de la prévision.

Une précision que je préfère donner que laisser découvrir : les 30 jours
d'historique visibles aujourd'hui sont **rejoués depuis les archives**, pas
scorés en direct. Ils sont marqués comme tels dans les données. Le premier
jour noté en conditions réelles, c'est maintenant.

Code, données quotidiennes et verdicts des stations qui échouent : tout est
ouvert.

🔗 [lien scoreboard] · [lien GitHub]

---

## Version 2 — plus courte, si tu veux tester le format bref

Une IA de prévision météo-océanique qui publie ses notes tous les jours,
y compris ses échecs.

Le modèle ne remplace pas la physique : il corrige l'erreur résiduelle du
modèle officiel, station par station. Et il est comparé à une baseline
nommée — le meilleur des 5 modèles de vagues (ou des 3 modèles de vent)
disponibles à cette position précise, pas à un épouvantail générique.

9 stations françaises : houle, niveau d'eau, vent. 7 publiées. Les 2 autres
sont mesurées chaque jour mais retenues par un gate qualité, parce que le
modèle n'y bat pas sa propre baseline. Elles restent dans le tableau, avec
leur verdict.

Meilleur résultat : −26 % d'erreur absolue hors biais aux Pierres Noires
(0,213 → 0,158 m).

Les 30 jours d'historique sont rejoués depuis les archives et marqués comme
tels. Le premier jour noté en direct, c'est aujourd'hui.

🔗 [lien] — code et données ouverts.

---

## Figure associée

**Recommandation : carrousel de 2 images, capture du site.** Une seule image
ne peut pas porter les deux affirmations du post (l'étendue du réseau, et la
baseline nommée station par station).

1. **La carte du réseau** (`ScoreboardMap`) — l'accroche. C'est le seul visuel
   du site qui reste lisible en vignette de fil LinkedIn, et il prouve la chose
   la plus difficile à inventer : 9 points sur de vraies côtes. Recadrer sur la
   façade Manche-Atlantique, sans le header de page.
2. **Le tableau, recadré sur 4-5 lignes** (`ScoreboardTable`) — la preuve.
   C'est la seule image qui montre que chaque station est comparée à un modèle
   *différent* et nommé (NCEP GFS-Wave, EWAM, ARPEGE), et qui affiche le
   verdict du gate sur Cherbourg. C'est ce que personne d'autre ne publie.

Si tu n'en veux qu'une : **le tableau**, pas la carte. Une carte de France
avec des points ressemble à toutes les cartes de France avec des points ; le
tableau est le seul contenu réellement distinctif. Il faut alors le recadrer
serré — 4 lignes maximum, lisibles sur mobile.

**Trois contraintes avant de capturer :**

- **Attendre le cron de 09:30 UTC et le déploiement.** Aujourd'hui le site
  affiche 6 stations, pas 9 : les stations de vent ne seront dans
  `stations.json` qu'après le run du jour. Une capture prise maintenant
  contredit le texte du post.
- **Jamais avec `?fixture=1`.** La fixture est réservée au dev et sert des
  données inventées — publier une capture de fixture serait exactement la
  faute que le post reproche aux autres.
- **Ne pas capturer un graphique de station.** Les courbes disponibles
  aujourd'hui portent des jours rejoués depuis les archives. Le post l'assume
  en texte ; une courbe mise en avant comme illustration principale, non.

## Notes de rédaction

- **Chiffre de tête = gain hors biais**, jamais le gain affiché. Le brut aux
  Pierres Noires est −25,8 %, le hors-biais −25,9 % : c'est la seule station
  où les deux coïncident, ce qui en fait le bon exemple à mettre en avant.
- **Ne pas dire « vent au parc »** si tu ajoutes Dieppe dans un post futur :
  ce sont des anémomètres côtiers à 10 m au-dessus du sol, un proxy du vent
  au large. Dieppe est à 8,6 km du parc mais à 40 m d'altitude et bien plus
  abritée (4,61 m/s de moyenne contre 7,75 à Ouessant).
- **Le gain vient de gate.json**, mesuré sur les données d'entraînement.
  Si quelqu'un demande « et en production ? », la réponse honnête aujourd'hui
  est : trop tôt, l'historique est rejoué. C'est justement pour ça que le
  scoreboard existe.
- Les deux angles restants pour les posts suivants : le vent aux sites EMR
  (Dieppe), et le détail de la méthode du gate.
