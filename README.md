# gen-admissions

Générateur de **données administratives hospitalières fictives** : passages aux
urgences (RPU), séjours (RSA), passages dans les unités (RUM) et mouvements entre
services, au format des tables que manipule un département d'information médicale.

Les données sont produites par une **simulation à événements discrets**
(bibliothèque [SimPy](https://simpy.readthedocs.io/)) : un hôpital virtuel « vit »
pendant un an, des patients arrivent aux urgences ou en entrée programmée, occupent
des lits, changent de service et sortent. Comme un service de 30 lits ne peut jamais
accueillir un 31e patient, les données obtenues portent les traces d'un vrai hôpital :
attentes aux urgences, hébergements dans un service voisin, tension qui monte en hiver.

> **Aucun patient réel.** Le dépôt ne contient que l'hôpital fictif
> `parametres/hopital_type/`, fabriqué de toutes pièces par
> `scripts/fabriquer_parametres_fictifs.py`. Les paramètres décrivant un vrai
> établissement — même s'il ne s'agit que de proportions — restent hors du dépôt
> (dossier `prod/`, ignoré par git).

## Démarrage rapide

```bash
pip install -e ".[dev]"

# Simuler un an d'activité de l'hôpital fictif (une vingtaine de secondes)
python -m gen_admissions parametres/hopital_type --sortie sorties/essai --duree 365 --graine 42

# Lancer les tests
pytest
```

La commande affiche le résumé des paramètres, celui de la simulation, puis le
**tableau de contrôle** : pour chaque indicateur (présents moyens, durée moyenne,
part des entrées par les urgences, par service…), la valeur observée dans les
paramètres, la valeur recalculée sur les tables simulées et la fourchette admise.
Le dossier de sortie contient les quatre tables en parquet et en CSV, la série
quotidienne de la tension, `resume.yaml` (graine, période, capacités, volumes) et
`controle.csv`.

Un exemple d'un an est versionné dans `sorties/exemple/` (parquet, contrôle et
résumé ; relancer la commande ci-dessus pour obtenir les CSV).

Options : `--nom` (nom de la simulation ; sans `--sortie`, les sorties vont dans
`<dossier_parametres>/simulations/<nom>`, ou `sorties/<nom>` à la racine du dépôt
pour l'hôpital fictif), `--chauffe` et `--marge` (jours de chauffe et de fin,
retirés des sorties), `--facteur-lits` (bornes de tension ajustées à un hôpital
agrandi), `--sans-csv`. Sans dossier de paramètres, la commande lit celui indiqué
dans `config_locale.yaml` (voir plus bas), sinon l'hôpital fictif.

## Ce que le simulateur produit

Le format est décrit colonne par colonne dans [`sorties/FORMAT.md`](sorties/FORMAT.md).
Les noms de colonnes et les codes sont ceux des tables réelles (`nas`, `d8eeue`,
`echpmsi`, `cdpu`…), de sorte qu'un traitement écrit pour les vraies tables tourne
sur les tables fictives sans modification ; une colonne `type` vaut `SIM` partout.

| Table | Une ligne par | Contenu principal |
| --- | --- | --- |
| `rpu` | venue aux urgences | arrivée et sortie (date-heure), âge, sexe, gravité CCMU, mode de sortie, séjour qui suit |
| `rsa` | séjour | entrée et sortie, GHM et niveau de sévérité, mode d'entrée et provenance (5 = urgences), mode de sortie et destination, nombre de RUM |
| `rum` | passage dans une spécialité | numéro d'ordre, spécialité (`spe_uma`), dates, modes d'entrée et de sortie PMSI (8 domicile, 6 mutation, 7 transfert, 9 décès), RUM principal |
| `mouvements` | lit occupé | spécialité, lit, dates, type (entrée, mutation, changement de lit) |

À ces colonnes s'ajoutent des colonnes **`sim_`**, la « vérité du simulateur » que
le réel ne dit jamais : situation de la venue, service prévu et rang du service
obtenu, attente d'un lit, supplément d'attente appliqué. On peut les ignorer.

## Comment ça marche

1. **Les venues aux urgences** arrivent selon un profil heure × jour de semaine,
   modulé par le mois. Chaque venue reçoit un âge, un sexe, puis une *situation*
   (consultation, hospitalisation, UHCD seule, transfert, décès) tirée en fonction
   de la saison et de la **classe de tension** du jour — la tension est le taux
   d'occupation des lits conventionnels, relevé chaque matin.
2. **Les entrées programmées** sont tirées service par service ; leur rythme est
   régulé chaque jour pour tenir le nombre de présents visé (loi de Little en
   boucle fermée), en tenant compte des entrées reportées faute de lit.
3. **Le séjour** est construit à partir du GHM : racine tirée selon la situation et
   l'âge, niveau de sévérité, durée totale, passage ou non en soins critiques, mode
   de sortie. La racine désigne le **premier service** ; la **table de passage**
   enchaîne ensuite les étapes, avec repli sur des tables plus générales quand une
   case contient trop peu de séjours.
4. **Le parc de lits** applique la règle d'admission : lit dans le service prévu ;
   sinon dans un service de **rang 1** (où la spécialité est habituellement prise en
   charge) ; sinon, après un supplément d'attente, dans un service de **rang 2**
   (exceptionnel) ; sinon transfert ou report. Les soins critiques ne se redirigent pas.
5. **Le journal** des événements devient les quatre tables, et le **contrôle**
   recalcule les indicateurs sur ces tables, avec les mêmes fonctions que
   l'extraction sur les données réelles, pour les comparer aux cibles.

### Organisation du code

Le paquet a deux moitiés : l'extraction prépare les paramètres, le simulateur les
fait vivre.

| Fichier | Rôle |
| --- | --- |
| `gen_admissions/extraction.py` | fonctions qui transforment des effectifs agrégés en fichiers de paramètres : normalisation par bloc avec seuils et repli, rangs, restriction à un site, tables de durées, profils ; vérification d'un dossier (`verifier_dossier`) ; cache parquet des requêtes (`memoriser`) ; `config_locale` |
| `gen_admissions/simulateur/parametres.py` | lecture et vérification des 22 fichiers, structure de l'hôpital, tables de tirage avec repli |
| `gen_admissions/simulateur/tirages.py` | les tirages : venue aux urgences, premier service, séjour (GHM, durée, soins critiques, sortie), suite du parcours |
| `gen_admissions/simulateur/lits.py` | le parc de lits et la règle d'admission (rang 1, rang 2, dernier recours, transfert) |
| `gen_admissions/simulateur/moteur.py` | les processus SimPy : flux des urgences, flux programmé régulé, passage aux urgences, séjour, relevé de tension |
| `gen_admissions/simulateur/export.py` | les tables au format `FORMAT.md`, `resume.yaml` |
| `gen_admissions/simulateur/controle.py` | indicateurs recalculés sur les sorties, tableau de contrôle, bilan |
| `gen_admissions/simulateur/__main__.py` | la ligne de commande (`python -m gen_admissions`) |
| `scripts/fabriquer_parametres_fictifs.py` | invente un monde fictif à deux sites et applique les fonctions d'extraction pour produire `parametres/hopital_type/` |
| `scripts/fabriquer_notebook_preparation.py` | engendre `notebooks/preparation_parametres.ipynb`, le modèle de notebook d'extraction sur données réelles |
| `scripts/fabriquer_notebook_cartographie.py` | engendre `notebooks/cartographie_hopital.ipynb`, qui décrit un hôpital à partir de ses paramètres |

## Les fichiers de paramètres

Un dossier de paramètres compte **22 fichiers** : `structure.yaml` (services, lits,
lits ouverts, soins critiques, règles d'attente), 20 tables CSV de proportions
(GHM, premier service, table de passage, durées, profils horaires, tension…) et
`cibles.csv` (fourchettes du contrôle). Chaque fichier est décrit dans
[`parametres/DICTIONNAIRE.md`](parametres/DICTIONNAIRE.md) ; les fonctions qui les
produisent, dans [`parametres/FONCTIONS.md`](parametres/FONCTIONS.md).

Trois conventions à connaître :

- les tables donnent des **proportions par bloc** (par exemple « sachant la racine
  de GHM et la classe d'âge ») ; un bloc trop petit (moins de 25 séjours) n'est pas
  écrit et le simulateur se **replie** sur un niveau plus général ;
- un service est de **rang 1** pour une spécialité s'il en reçoit au moins 10 % des
  séjours, de **rang 2** sinon ; cette distinction porte la règle d'admission ;
- les tables à plusieurs sites sont **restreintes** au chargement aux services de
  l'hôpital simulé, et les rangs sont recalculés.

### Refabriquer l'hôpital fictif

```bash
python scripts/fabriquer_parametres_fictifs.py --dossier parametres/hopital_type
```

### Préparer les paramètres d'un vrai établissement

Le notebook `notebooks/preparation_parametres.ipynb` est le modèle : chaque cellule
interroge l'entrepôt, agrège, puis appelle une fonction d'`extraction.py` qui écrit
un fichier. Les chemins propres à la machine (dossier des paramètres, cache des
requêtes, site) se mettent dans `config_locale.yaml` à la racine du dépôt, à copier
depuis `config_locale.exemple.yaml` ; ce fichier, le dossier `prod/` et les
notebooks qui touchent aux données réelles ne sont pas suivis par git.

## Reproductibilité

Une graine unique (`--graine`) fixe tous les tirages ; elle est enregistrée dans
`resume.yaml` avec la période, les capacités et le dossier de paramètres. Deux
simulations de même graine sur les mêmes paramètres donnent les mêmes tables.

## Limites connues

- Pas de diagnostics ni d'actes : le GHM est tiré directement. Pas d'identité, pas
  de date de naissance (seulement l'âge), pas d'unités plus fines que la spécialité.
- Les lits ouverts sont constants sur l'année ; les fermetures saisonnières sont
  prévues (profil de fermeture par service), pas encore écrites.
- La suite du parcours ne « se souvient » que du service précédent ; sur les
  parcours longs, un patient peut être envoyé dans un service inattendu.
- Les retours de patients déjà connus sont simplifiés (probabilité de retour fixe).
