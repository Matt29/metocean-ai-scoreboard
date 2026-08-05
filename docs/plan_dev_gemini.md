# Plan de développement stratégique

## Scoreboard Metocean IA : De la Vitrine Open-Data au Produit B2B Phare

**Éditeur :** OceanData Consulting
**Horizon :** 2026 – 2027
**Document :** Plan Produit & Business

*OceanData Consulting — Confidentiel & Propriétaire*

---

## Executive Summary & Vision Produit

Le Scoreboard Metocean IA répond à un enjeu clé du secteur maritime : la transparence et l'auditabilité des modèles d'IA face aux prévisions physiques officielles (Météo-France MFWAM/ARPEGE, ECMWF IFS, DWD, NCEP, marées harmoniques). En confrontant quotidiennement l'IA aux mesures réelles (bouées, marégraphes, stations de vent), il établit une preuve de concept incontestable ("Skin in the game"). Ce document définit la roadmap pour transformer cette vitrine en un produit B2B phare (SaaS & API Metocean Post-Processing).

---

## AXE 1 : Refonte technique, architecture & SEO

### 1. Diagnostic de l'existant

- **Rendu Client-Side (SPA)** : Le chargement dynamique du fichier stations.json par le navigateur crée un risque d'écran d'erreur en cas de latence réseau ou de lecture sans JavaScript.
- **SEO & Canonical** : Les sous-pages stations (ex: /scoreboard/anglet) partagent la balise canonical globale, ce qui freine leur indexation individuelle par les moteurs de recherche.
- **Partage Social (Open Graph)** : Absence de visuels d'aperçu personnalisés lors du partage de liens de stations sur LinkedIn/X.

### 2. Plan d'action technique

- **Migration vers un Rendu Statique / Incrémental (SSG / ISR)** : Compiler le HTML final lors du run quotidien (GitHub Actions + Astro/Next.js/Static Engine). Temps de chargement < 50ms, résilience à 100%, lisibilité garantie pour les crawlers.
- **SEO Structuré & Open Graph Dynamique** : Générer automatiquement pour chaque station une image d'illustration dynamique (score du jour, graphique résumé) et des méta-données optimisées.
- **Monitor & Badge de Fraîcheur** : Affichage explicite de l'horodatage UTC de la dernière exécution du pipeline et du statut des flux de données.

---

## AXE 2 : UX, visualisation de données & métriques avancées

### 1. Carte Interactive Métocéan (Homepage Scoreboard)

Implémenter une carte vectorielle dynamique (MapLibre / Leaflet) géolocalisant les capteurs avec un code couleur métier :

- **VERT** : L'IA améliore la prévision physique (Skill Score > 0).
- **ROUGE** : La physique officielle reste supérieure (IA recalée / station affichée sans prévision IA).
- **GRIS** : Capteur en maintenance ou données d'observation momentanément indisponibles.

### 2. Enrichissement des pages stations dédiées (ex: /scoreboard/anglet)

- **Décomposition par Échéance (Lead Time)** : Évaluation de l'erreur par terme de prévision (H+6, H+12, H+24, H+48, H+72) pour mettre en évidence l'apport du post-traitement IA à court terme.
- **Zoom sur Extrêmes & Tempêtes** : Analyse dédiée aux coups de mer récents avec calcul de la Peak Error (erreur sur le pic de hauteur de houle ou vent max).
- **Tableau Multi-Métriques** : Skill Score (%), RMSE, MAE, Correction de Biais et Corrélation (R²).
- **Filtres Temporels Interactifs** : Sélection des fenêtres glissantes (7, 30, 90 jours) avec superposition des courbes d'observation, de la physique et de l'IA.

---

## AXE 3 : Diffusion, inbound marketing & acquisition

| 1. Contenu viral | 2. Vitrine public | 3. Lead magnet | 4. Pipeline B2B |
|---|---|---|---|
| Baromètre Métocéan IA mensuel & posts LinkedIn | Scoreboard interactif & carte de performance | Export CSV des séries & Widget intégrable | Prospection qualifiée & démos grands comptes |

- **Baromètre Trimestriel / Mensuel** : Publication d'analyses d'impact lors d'événements métocéaniques majeurs (ex: gains de précision de l'IA pendant les tempêtes d'automne).
- **Widget Intégrable "Verified by Metocean Scoreboard"** : Composant web / iframe permettant à des acteurs tiers (capitaineries, portail météo, clubs nautiques) d'afficher le score de leur station localement, générant des backlinks SEO à haute autorité.
- **Capture de Leads Qualifiés** : Déblocage de l'export CSV des données historiques et de l'accès API Open-Data contre saisie de l'email professionnel du prospect.

---

## AXE 4 : Roadmap de commercialisation B2B (offres produits)

| Offre Produit | Cible Privilégiée | Modèle Économique | Valeur Ajoutée Key |
|---|---|---|---|
| **1. Private Scoreboard** | Parcs éoliens offshore, Ports, Chantiers navals | SaaS (Abonnement mensuel / annuel) | Intégration de capteurs privés et calibration sur-mesure d'un dashboard de suivi de performance. |
| **2. Metocean AI Forecast API** | Routage maritime, Bureaux d'études EMR, Logiciels SaaS | API REST Pay-per-use / Pass mensuel | Flux temps réel de prévisions métocéaniques corrigées par IA à haute résolution (H+1 à H+72). |
| **3. Risk & Uncertainty Engine** | Assureurs maritimes, Ingénierie offshore | SaaS Add-on / Ingestion sur-mesure | Prévisions probabilistes (Conformal Prediction) fournissant les seuils de risque opérationnel. |
| **4. On-Premise & Licensing** | Grands Instituts, Marine, Majors de l'Énergie | Licence logicielle + Maintenance | Déploiement complet de la chaîne de valeur (Pipeline + IA + UI) sur l'infrastructure du client. |

---

## Planning d'exécution sur 12 mois

| Période | Jalon Technique & Marketing | Livrables Clés |
|---|---|---|
| Q3 2026 | Refonte SSG & Moteur SEO | Architecture SSG opérationnelle, Meta OpenGraph dynamique par station. |
| Q4 2026 | UX Avancée & Carte Interactive | Carte MapLibre globale, séries temporelles par échéance (Lead Time). |
| Q1 2027 | Baromètre & Lead Capture | Publication du Baromètre #1, module Widget Web, export CSV conditionné. |
| Q2 2027 | Lancement Commercial B2B | Pilote "Private Scoreboard", API Metocean AI commerciale documentée. |

---

*OceanData Consulting — Modélisation océanique, analyse marine et IA pour des prévisions côtières fiables.*
*Contact : contact@oceandataconsulting.fr | Site : oceandataconsulting.fr*

---

## AVANCEMENT — 2026-08-05 (enrichissement du contrat JSON, côté données)

Réalisé (5 commits, +32 tests, 272 passed) :

•  AXE 1 / Monitor & Badge de Fraîcheur (côté données) : horodatage du run
   (updated) et statut par station dans le contrat — commit 934a15a.
•  AXE 2.2 / Décomposition par Échéance : by_lead (H+6…H+72, fenêtre 30 j)
   dans scores.json — commit a5415c1.
•  AXE 2.2 / Tableau Multi-Métriques : metrics_30d (RMSE, biais, R²) par
   station dans scores.json — commit d241e4f.
•  AXE 2.1 / Carte (côté données) : déjà satisfait — lat/lon existants ;
   vert/rouge/gris dérivable de published × status. Aucun champ ajouté.
•  AXE 2.2 / Zoom sur Extrêmes : extremes.json (pics observés + peak error,
   ≤3 épisodes) écrit par le run daily — commit 216f857.
•  AXE 3 / Export CSV des séries : series.csv par station (hors contrat
   versionné) — commit e4f342c.

Reste côté site (WEB/ODC_WEBSITE) : consommer status, by_lead, metrics_30d,
updated, extremes.json et les CSV. Surveiller le premier run daily
(cron 07:30 UTC) qui écrira extremes.json et les series.csv en prod.

### AVANCEMENT — 2026-08-05 (après-midi) : consommation côté site ODC

Le « Reste côté site » ci-dessus est fait — commit `602584a` du repo
`oceandata-site`, déployé en production sur Vercel (oceandataconsulting.fr).

- **AXE 1 / Badge de Fraîcheur** : horodatage UTC du dernier run + état des
  flux affichés sur la page scoreboard.
- **AXE 2.1 / Code couleur carte et tableau** : vert (l'IA améliore) / rouge
  (physique supérieure) / gris (flux sans données à jour), sur les marqueurs
  de la carte et en pastille dans le tableau — jamais la couleur seule
  (libellés accessibles systématiques).
- **AXE 2.2 / Décomposition par échéance** : barres groupées MAE IA vs
  physique (H+6, H+12, H+24, H+48, fenêtre 30 j) sur la page station, avec
  légende dérivée des données (pas de conclusion codée en dur).
- **AXE 2.2 / Tableau Multi-Métriques** : Skill Score, MAE, RMSE, biais, R²
  (IA vs physique, 30 j) sur la page station.
- **AXE 2.2 / Zoom sur Extrêmes** : épisodes récents avec pic observé et
  peak error signée IA vs physique.
- **AXE 3 / Export CSV** : lien de téléchargement des séries par station.

Tout est rétrocompatible : chaque section reste masquée tant que le pipeline
n'a pas encore publié les champs correspondants (premier run daily à venir).
Méthode : quatre sous-agents en parallèle (données, homepage, page station,
docs) + une passe de vérification adversariale avant commit — règle inscrite
au CLAUDE.md du projet.

Restent du plan, non couverts : migration SSG/ISR, Open Graph dynamique et
canonical par station (AXE 1) ; carte MapLibre vectorielle (AXE 2.1 — la
carte actuelle est du SVG maison) ; filtres temporels 7/30/90 j (AXE 2.2 —
demande d'abord une fenêtre 90 j côté pipeline) ; widget intégrable,
baromètre, capture de leads (AXE 3) ; offres B2B (AXE 4).

---

## Cohérence avec le plan interne (plan_developpement_scoreboard_metocean.pdf)

**Constat principal** : `plan_dev_gemini.md` est l'export texte brut de ce même PDF —
les deux documents portent un contenu identique (executive summary, 4 axes, tableau
d'offres B2B, planning 12 mois). Il ne s'agit donc pas de deux plans distincts mais
d'une seule source, dans deux formats. La comparaison ci-dessous porte donc sur la
cohérence entre le PDF source et l'état d'avancement réel du dépôt (section
AVANCEMENT), pas entre deux visions divergentes.

**Convergences** :
- Les 5 commits listés dans AVANCEMENT correspondent précisément à des items du
  plan (Monitor & Badge de Fraîcheur → AXE 1 ; by_lead et metrics_30d → AXE 2.2 ;
  extremes.json → AXE 2.2 « Zoom sur Extrêmes » ; series.csv → AXE 3 « Lead Magnet »).
- La numérotation des jalons AVANCEMENT référence explicitement les AXE du plan
  (AXE 1, AXE 2.1, AXE 2.2, AXE 3), preuve d'un suivi discipliné du plan d'origine.

**Divergences / points d'attention** :
- Le plan prévoit une **Migration SSG/ISR** (AXE 1) et une **carte interactive
  MapLibre/Leaflet** (AXE 2.1) comme jalons Q3/Q4 2026 ; l'AVANCEMENT ne couvre que
  le *contrat de données* (JSON), pas le rendu front. Le texte le confirme lui-même :
  « Reste côté site (WEB/ODC_WEBSITE) : consommer… ». Le travail front (SSG, carte,
  Open Graph dynamique) reste donc entièrement à faire malgré l'avancement data.
- AXE 3 (Widget intégrable, Baromètre, capture de leads par email) et AXE 4 (offres
  B2B, pilote Private Scoreboard) n'ont aucun avancement rapporté — cohérent avec le
  planning qui les place en Q1/Q2 2027, plus tard que les items déjà traités.
- Le tableau Multi-Métriques du plan cite Skill Score, RMSE, MAE, Biais, R² ; le
  commit d241e4f livre RMSE, biais, R² (`metrics_30d`). Vérifié : les deux métriques
  restantes sont déjà couvertes — la MAE figure dans `scores.json` depuis l'origine
  (`mae_ia_30d`, `mae_baseline_30d`…) et le Skill Score se dérive de la paire MAE
  IA/baseline, dérivation que le site effectue déjà pour son verdict. Aucun écart.

**Éléments présents dans un seul document** :
- Le PDF/plan contient tout le volet stratégique (offres B2B, planning 12 mois,
  contact) absent de toute section « avancement » côté data.
- L'AVANCEMENT contient des détails d'implémentation (noms de commits, nombre de
  tests, chemins de fichiers) absents du plan stratégique, par nature.
