# gen-admissions

Générateur de **données administratives hospitalières fictives** : passages aux
urgences, séjours, passages dans les unités (RUM) et mouvements entre services.

Les données sont produites par une **simulation à événements discrets**
(bibliothèque [SimPy](https://simpy.readthedocs.io/)) : un hôpital virtuel
« vit » pendant un an, des patients arrivent, occupent des lits, changent de
service et sortent. Comme un service de 30 lits ne peut jamais accueillir un
31e patient, les données obtenues sont crédibles : attentes aux urgences,
hébergements dans un autre service, taux d'occupation réalistes.

> **Aucun patient réel.** Les paramètres de l'hôpital type livré ici ont été
> fabriqués de toutes pièces (`scripts/fabriquer_parametres.py`). Le jour où
> l'on disposera des agrégats d'un vrai établissement, il suffira de remplacer
> les fichiers du dossier `parametres/`, sans toucher au code.

## Démarrage rapide

```bash
pip install -e ".[dev]"

# Simuler un an d'activité de l'hôpital type (une dizaine de secondes)
python -m gen_admissions simuler parametres/hopital_type sorties/essai --graine 42

# Lancer les tests
pytest
```

Le dossier de sortie contient les tables (CSV et Parquet), un fichier
`description.json`, et le **tableau de contrôle** (`controle.md`) qui compare
les indicateurs du jeu de données aux cibles. Un jeu de données exemple d'un an
est versionné dans `sorties/exemple/`.

## Ce que le simulateur produit

| Table | Une ligne par | Contenu principal |
| --- | --- | --- |
| `rpu` | passage aux urgences | dates et heures d'entrée et de sortie, âge, sexe, gravité CCMU, mode de sortie, service de destination |
| `rss` | séjour | dates d'entrée et de sortie, mode d'entrée, provenance (5 = urgences), mode de sortie et destination (codes PMSI), nombre de RUM |
| `rum` | passage dans une unité | rang, unité, dates (au jour), modes d'entrée et de sortie PMSI (6 mutation, 7 transfert, 8 domicile, 9 décès), indicateur d'hébergement, spécialité prévue, lit |
| `mouvements` | changement de lit ou d'unité | date et heure, unité et lit de départ et d'arrivée, type (entrée, mutation, sortie) |
| `verite_sejours` | séjour | ce qui a réellement été simulé : trajectoire prévue (catalogue ou table de passage), trajectoire réalisée, hébergements, retours, attentes |
| `verite_passages` | passage | attente d'un lit, transfert faute de lit |
| `patients` | patient | âge, sexe, nombre de venues |

Exemple d'un séjour avec hébergement (table `rum`) : le patient relève de la
cardiologie, mais le service est plein ; il est hébergé en pneumologie, puis
rejoint la cardiologie dès qu'un lit se libère.

| rang | unite | date_entree | date_sortie | mode_entree | mode_sortie | heberge | specialite_prevue |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | PNEUMO | 2026-02-12 | 2026-02-12 | 8 | 6 | oui | CARDIO |
| 2 | CARDIO | 2026-02-12 | 2026-02-23 | 6 | 6 | non | CARDIO |
| 3 | REA | 2026-02-23 | 2026-03-01 | 6 | 6 | non | REA |
| 4 | CARDIO | 2026-03-01 | 2026-03-02 | 6 | 8 | non | CARDIO |

## Comment ça marche

Le fonctionnement est décrit en détail dans le plan d'analyse du projet. En
résumé :

1. **Les arrivées aux urgences** suivent un rythme qui varie selon l'heure, le
   jour de la semaine et le mois (méthode d'« amincissement »). Chaque patient
   reçoit un âge, un sexe et une gravité ; l'issue du passage (domicile,
   hospitalisation, transfert, décès, parti sans attendre) en dépend.
2. **Les entrées programmées** sont tirées chaque jour, service par service,
   avec un profil hebdomadaire et horaire.
3. **Le parcours** d'un patient hospitalisé vient soit du **catalogue de
   trajectoires** (parcours complets, fréquents), soit de la **table de
   passage** (étape par étape, avec repli sur des tables plus générales quand
   une case contient trop peu de séjours).
4. **La durée** de chaque passage est tirée dans une loi log-normale propre au
   service, au mode d'entrée et à la tranche d'âge, puis ajustée à une heure
   de sortie plausible (surtout en fin de matinée, moins le week-end).
5. **Le gestionnaire de lits** applique les règles : lit libre → entrée ;
   sinon attente avec priorité ; au-delà d'un délai, hébergement dans un
   service compatible (avec retour dans le service prévu dès qu'un lit s'y
   libère) ; au-delà d'un délai plus long, transfert vers un autre établissement.
6. **Le journal** des événements est transformé en tables, et le **contrôle**
   compare les indicateurs aux cibles.

### Organisation du code

| Fichier | Rôle |
| --- | --- |
| `gen_admissions/config.py` | lecture et vérification des fichiers de paramètres |
| `gen_admissions/hasard.py` | flux de hasard séparés (une graine, plusieurs générateurs indépendants) |
| `gen_admissions/distributions.py` | loi log-normale à partir de la médiane et des quartiles, profils horaires |
| `gen_admissions/parcours.py` | catalogue de trajectoires, table de passage avec repli, durées |
| `gen_admissions/lits.py` | gestionnaire de lits : attente, hébergement, retour, transfert |
| `gen_admissions/moteur.py` | les processus SimPy : générateurs de flux, passage aux urgences, séjour |
| `gen_admissions/modele.py` | les objets du journal : patient, passage, séjour, RUM, mouvement |
| `gen_admissions/export.py` | construction des tables RPU, RSS, RUM, mouvements, vérité |
| `gen_admissions/controle.py` | indicateurs, comparaison aux cibles, graphique d'occupation |
| `gen_admissions/extraction.py` | calcul des paramètres agrégés à partir de données de type RUM (réutilisable sur des données réelles) |
| `scripts/fabriquer_parametres.py` | fabrique les paramètres de l'hôpital type fictif |
| `notebooks/prise_en_main.ipynb` | visite guidée : lancer une simulation, regarder les tables, retrouver les mécanismes |

## Les fichiers de paramètres

Tout est dans `parametres/hopital_type/`. La structure et les règles sont dans
un fichier YAML ; les grandes tables sont des CSV, modifiables dans un tableur.

| Fichier | Contenu |
| --- | --- |
| `structure.yaml` | services et nombre de lits, services d'hébergement compatibles, contraintes d'âge et de sexe, règles d'attente, réglages de simulation |
| `profil_arrivees_urgences.csv` | poids des arrivées par heure, jour de la semaine et mois |
| `profil_patients_urgences.csv`, `gravite_urgences.csv` | répartition des patients par âge, sexe et gravité |
| `issues_urgences.csv`, `durees_passage_urgences.csv` | issue du passage selon l'âge et la gravité ; durée du passage selon l'issue |
| `entrees_programmees.csv`, `profil_entrees_programmees.csv`, `profil_patients_programme.csv` | admissions programmées par service, profils hebdomadaire et horaire, profil des patients |
| `premier_service.csv` | premier service d'hospitalisation selon le mode d'entrée, l'âge et le sexe |
| `catalogue_trajectoires.csv` | trajectoires fréquentes avec leur effectif |
| `table_passage.csv` | suite du parcours à la fin d'un passage, à quatre niveaux de détail |
| `durees_sejour.csv` | médiane, quartiles et part des passages de moins d'un jour, par service, mode d'entrée et âge |
| `profil_sorties.csv` | heures de sortie |
| `cibles.csv` | fourchettes attendues pour les indicateurs de contrôle |

Un point important, découvert en construisant le prototype : **la table de
passage est calculée sur les séjours que le catalogue ne couvre pas** (voir
`extraction.hors_catalogue`). Si elle était calculée sur tous les séjours,
les parcours rares (passages en réanimation, par exemple) seraient
sous-représentés, car le catalogue ne retient que les trajectoires fréquentes.

### Refabriquer les paramètres fictifs

```bash
python scripts/fabriquer_parametres.py parametres/hopital_type
```

Le script décrit une « réalité » fictive (règles médicales simples, volumes,
durées), en tire trois ans de séjours sans contrainte de lits, ajuste les
entrées programmées pour approcher des taux d'occupation visés, puis applique
le module `extraction` pour produire les fichiers. Ce même module servira
pour les données réelles : il n'a besoin que d'un tableau de passages d'unités
(colonnes décrites en tête de `gen_admissions/extraction.py`).

## Reproductibilité

Une graine unique (`--graine`) fixe tous les tirages ; elle est enregistrée
dans `description.json`. À partir de cette graine, le simulateur crée
plusieurs flux de hasard indépendants (arrivées, durées, parcours…) : modifier
une partie du modèle ne perturbe pas les autres.

## Limites de cette première version

- Pas de données médicales (ni diagnostic, ni GHM). Les parcours sont rendus
  cohérents par l'âge, le sexe, le mode d'entrée et le service précédent.
- Les retours de patients déjà connus sont simplifiés : le taux de
  réhospitalisation à 30 jours est faible. Une population plus réaliste est
  prévue pour l'étape suivante.
- Pas encore de mode « données sales », de fermetures de lits saisonnières ni
  d'ajustement automatique des paramètres.
- Dans la table de passage, le patient ne « se souvient » que du service
  précédent : sur les parcours longs non couverts par le catalogue, il peut
  arriver qu'un patient soit envoyé dans un service inattendu. C'est une
  limite connue, utile pour tester la robustesse du code d'analyse.
