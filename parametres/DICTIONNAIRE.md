# Dictionnaire des paramètres du simulateur (version 2)

Ce document décrit chaque fichier que le simulateur lit, colonne par colonne,
avec la façon de le calculer à partir de données réelles (PMSI, RPU,
mouvements). Il correspond à la section 3 de la *Spécification des deux
usages du simulateur de flux hospitaliers* ; les étapes citées (A1, B3, C5, D…)
renvoient au mécanisme décrit dans sa section 2.3.

Le même module de code (`gen_admissions/extraction.py`) produit ces fichiers,
que les données d'entrée soient la réalité fictive du script
`scripts/fabriquer_parametres.py` ou des données réelles. Le dictionnaire et
le code doivent donc toujours dire la même chose.

---

## 1. Conventions communes à tous les fichiers

**Format.** Fichiers CSV, séparateur virgule, point décimal, encodage UTF-8,
une ligne d'en-tête. Les proportions sont arrondies à six décimales. Le
fichier de structure est en YAML.

**Proportions, jamais d'effectifs.** Chaque table de tirage contient une
colonne `part`. Les parts somment à 1 à l'intérieur d'un *bloc* (par exemple
« pour un mois et une classe d'âge donnés »). Le bloc est indiqué pour chaque
fichier. Aucun fichier ne contient de nombre de séjours ou de patients.

**Seuil d'effectif et catégories rares.** Lors du calcul sur des données
réelles, une case (une ligne) n'est retenue telle quelle que si elle repose sur
au moins `seuil_effectif` séjours (100 séjours sur 5 ans par défaut). Les cases
sous le seuil d'un même bloc ne sont pas supprimées : on calcule leur masse
totale (la somme de leurs effectifs divisée par l'effectif du bloc), puis on la
répartit **uniformément** entre elles. Le volume des cas rares est ainsi
conservé, sans qu'aucune proportion individuelle sous le seuil ne soit
transmise. Dans les tables de services qui portent une colonne `rang`, ces
lignes complétées reçoivent le rang 3.

**Repli.** Quand une case n'existe pas (combinaison jamais observée), le
simulateur utilise une table plus générale, indiquée dans la colonne
« Repli » de chaque fichier. Pour que le repli soit possible, le fichier
contient aussi les lignes des niveaux de repli (par exemple, dans
`niveaux.csv`, les lignes par racine seule en plus des lignes par racine ×
classe d'âge), identifiées par une valeur vide ou `*` dans la colonne
détaillée.

**Classes d'âge.** Deux jeux, selon la finesse des tables :

| Jeu | Valeurs | Utilisé par |
| --- | --- | --- |
| `classe_age` | `0-17`, `18-64`, `65-74`, `75+` | toutes les tables par racine de GHM, par spécialité, et les profils des urgences |
| `classe_age_duree` | `0-59`, `60+` | les tables par GHM complet (`durees_ghm`, `soins_critiques_ghm`), où le croisement avec 2 639 GHM impose des classes larges |

Les bornes sont des âges révolus à l'entrée.

**Vocabulaire des codes.**

| Colonne | Valeurs | Sens |
| --- | --- | --- |
| `mode_entree` | `urgences`, `programme` | séjour venu des urgences (provenance 5 du RSS) ou non |
| `situation` (venue aux urgences) | `consultation`, `hospitalisation`, `mono_uhcd`, `transfert`, `deces`, `parti` | consultation = retour à domicile sans séjour ; hospitalisation = séjour avec au moins un service après l'UHCD ; mono_uhcd = séjour limité à l'UHCD ; transfert = vers un autre établissement ; parti = parti sans attendre |
| `mode_sortie` (séjour) | `domicile`, `SSR`, `EHPAD`, `transfert`, `deces` | codes PMSI 8, 7-2, 8-7, 7-1, 9 |
| `niveau` | `1`, `2`, `3`, `4`, `J`, `T`, `Z`, `A`, `B`, `C`, `D`, `E` | sixième caractère du GHM |
| `periode` | `1` à `12` | mois civil |
| `classe_tension` | `basse`, `moyenne`, `haute` | voir `classes_tension.csv` |
| `tranche_h` | `0-1`, `1-2`, `2-4`, `4-6`, `6-12`, `12-24`, `24+` | tranches de durée en heures, borne basse incluse |
| `duree_j` | `0` à `60`, puis `60+` | durée de séjour en jours entiers (date de sortie moins date d'entrée) |
| `rang` | `1`, `2`, `3` | 1 service habituel (part ≥ 10 %) ; 2 service exceptionnel (observé au-dessus du seuil d'effectif, part < 10 %) ; 3 service complété par la règle des catégories rares (effectif sous le seuil). Le rang croît avec la rareté : plus petit = mobilisé en premier |

**Racine de GHM.** Les cinq premiers caractères du GHM (par exemple `05M09`).
Le GHM complet ajoute le niveau (`05M093`).

**Spécialités et services.** Les tables parlent en *spécialités*
(`specialites.csv`) ; le fichier de structure indique à quelle spécialité
chaque *service* de l'hôpital correspond. Dans l'hôpital fictif, chaque
service a sa propre spécialité ; dans un établissement réel, plusieurs unités
peuvent partager une spécialité.

**L'UHCD.** L'unité d'hospitalisation de courte durée fait partie de la
structure des urgences : dans `structure.yaml`, le bloc `urgences:` nomme
le service qui reçoit les arrivées (`service: URG`), et les lits de ce
service sont ceux de l'UHCD. Les passages aux urgences
n'occupent pas de lit et n'ont pas de GHM ; un séjour en UHCD est, lui, un
séjour (un RUM dans l'unité d'UHCD, donc un RSS et un GHM). L'UHCD est
traitée comme un prolongement des urgences, jamais comme une étape du parcours.

La règle d'entrée en UHCD (étape A6) : seules les venues de situation
`hospitalisation` ou `mono_uhcd` peuvent occuper un lit d'UHCD (un lit
d'UHCD est un RUM, donc un séjour) ; les autres situations restent dans les
box des urgences quelle que soit leur durée. Le lit est demandé à la 6e heure
du passage (`seuil_uhcd_h`) : pour `mono_uhcd`, il est occupé jusqu'à la fin
de la durée tirée en A4 (durée totale urgences + UHCD, plancher 6 h), sans
recours à `durees_ghm` ; pour `hospitalisation`, il n'est demandé que si la
durée tirée dépasse 6 h, et il est gardé jusqu'à l'obtention du lit de
service. Faute de lit d'UHCD, le patient reste aux urgences (lit brancard)
et en prend un s'il s'en libère avant la fin de son passage. Dans toutes
les tables de parcours (premier service, table de passage), les
passages en UHCD sont retirés avant calcul : le premier service d'un séjour
est le premier service *autre que l'UHCD*. Un séjour qui se limite à l'UHCD
est la situation `mono_uhcd` de `venues_urgences.csv`.

---

## 2. Structure et références

### `structure.yaml` — l'hôpital et ses règles

Décrit ce qui ne se mesure pas dans le PMSI : la capacité, les règles de
fonctionnement, les réglages. C'est le seul fichier qui contient des nombres
absolus (lits, passages par an).

```yaml
etablissement: HOPITAL_FICTIF          # identifiant repris dans les sorties
description: texte libre
debut: '2026-01-01'                    # premier jour simulé
classes_age: [0-17, 18-64, 65-74, 75+]
classes_age_duree: [0-59, 60+]

services:                              # un bloc par service
  URG:
    libelle: Urgences (dont UHCD)
    specialite: URG
    lits: 10                           # les lits de l'UHCD, demandés au-delà de seuil_uhcd_h ;
                                       # les passages eux-mêmes n'occupent pas de lit
  REA:
    libelle: Réanimation et surveillance continue
    specialite: REA
    lits: 32
    soins_critiques: [rea, usc]        # types couverts (rea, usi, usc) ; pas de redirection
                                       # vers ou depuis un service conventionnel
  USIC:
    libelle: Soins intensifs cardiologiques
    specialite: USIC
    lits: 8
    soins_critiques: [usi]
  CARDIO:
    libelle: Cardiologie
    specialite: CARDIO
    lits: 36
    occupation_visee: 0.90             # sert au calcul des entrées programmées (B1)
  PED:
    libelle: Pédiatrie
    specialite: PED
    lits: 28
    age_max: 17                        # garde-fou ; le casemix suffit en principe
  GYN_OBS:
    libelle: Gynécologie-obstétrique
    specialite: GYN_OBS
    lits: 34
    sexe: F                            # garde-fou
  # …

urgences:
  service: URG                         # le service qui reçoit les arrivées (ses lits sont ceux de l'UHCD)
  passages_par_an: 45000               # volume absolu, multiplié par les profils (A1)
  seuil_uhcd_h: 6                      # durée de passage au-delà de laquelle un lit d'UHCD est demandé (A6)
  delai_transfert_h: 24                # attente de lit au-delà de laquelle un patient des urgences est transféré (D7)

lits:
  delai_report_programme_h: 8          # attente au-delà de laquelle une entrée programmée est reportée au lendemain (B4)
  heure_releve_tension: 8              # heure du calcul quotidien de la tension (D1)
  priorites:                           # plus petit = servi en premier (D5)
    sortie_soins_critiques: 0
    urgences: 1
    mutation: 1
    programme: 2

sorties:
  facteur_weekend: 0.45                # sorties du samedi et du dimanche, en proportion d'un jour de semaine (C8)
  services_sans_effet_weekend: [REA, USC, USIC, URG]

population:
  probabilite_retour:                  # part des arrivants qui sont des patients déjà connus (E)
    0-17: 0.10
    18-64: 0.12
    65-74: 0.20
    75+: 0.28

simulation:
  duree_jours: 365
  chauffe_jours: 42
  marge_fin_jours: 60
  graine: 42

autorisations_soins_critiques:         # lu par l'extraction, pas par la simulation :
  rea: ["01A", "01B"]                  # types d'autorisation d'unité médicale qui définissent
  usi: ["02A", "02E", "02F", "18"]     # chaque type, pour classer les services comme les séjours
  usc: ["03A", "03B"]
```

Ce que ce fichier ne contient plus par rapport à la version 1 : la liste des
services d'hébergement compatibles, le délai avant hébergement, le retour dans
le service d'origine, la priorité selon la gravité CCMU, le taux d'occupation
global. L'hébergement hors de la spécialité est lu dans les tables de
premier service et de passage (colonne `rang`).

**Soins critiques.** La clé `soins_critiques` d'un service est la liste des
types qu'il couvre, parmi `rea` (réanimation), `usi` (soins intensifs) et
`usc` (surveillance continue). Un service peut en couvrir plusieurs
(réanimation et surveillance continue réunies). Un service sans cette clé
est un service conventionnel. Cette liste sert à l'étape C5 : un séjour dont
le tirage C4 a donné un type de soins critiques ne peut suivre qu'une
trajectoire passant par un service qui couvre ce type. Il est donc
recommandé de classer les services d'après le type d'autorisation de leurs
unités, avec les listes du bloc `autorisations_soins_critiques`, qui servent
aussi à classer les séjours (`soins_critiques_ghm.csv`). Il est
indispensable que les unités de soins intensifs (soins intensifs
cardiologiques, unité neuro-vasculaire…) aient leur propre spécialité dans
`specialites.csv` et dans la correspondance unités → spécialités utilisée
pour calculer les tables de parcours : rattachées à la cardiologie ou à la
neurologie, elles disparaîtraient des trajectoires.

**Comment fixer les règles.** Les délais, priorités et seuils sont des
pratiques de l'établissement, pas des mesures. On leur donne une valeur de
départ (celles ci-dessus), puis on les ajuste jusqu'à ce que le tableau de
contrôle reproduise les indicateurs observés (part de premiers services de
rang 2, lits brancards, occupation de l'UHCD, taux de transfert, le tout par
classe de tension). Le délai de transfert peut être approché par un centile
élevé (95e ou 99e) de la durée de passage des patients hospitalisés.

### `specialites.csv` — la liste des spécialités

| Colonne | Contenu |
| --- | --- |
| `specialite` | code court, sans espace (`CARDIO`) |
| `libelle` | libellé lisible |
| `type` | `medecine`, `chirurgie`, `obstetrique`, `pediatrie`, `soins_critiques`, `urgences` |

Les codes doivent être exactement ceux des colonnes `specialite`, `service`,
`precedent`, `suite` des autres tables et du champ `specialite` de la
structure. Les séjours limités à l'UHCD ne sont pas une spécialité : ils sont la
situation `mono_uhcd` de `venues_urgences.csv`.

### `regroupements_ghm.csv` — la nomenclature des GHM

Dérivé du fichier de regroupements de l'ATIH (une ligne par GHM).

| Colonne | Contenu |
| --- | --- |
| `ghm` | GHM complet (`05M093`) |
| `racine_ghm` | cinq premiers caractères |
| `niveau` | sixième caractère |
| `libelle` | libellé du GHM |
| `ga`, `gp`, `da` | groupe d'activité, groupe de planification, domaine d'activité (codes ATIH), facultatifs |

Ce fichier est une nomenclature, pas une table de tirage : il donne la liste
des GHM valides, leur racine, leur niveau et leur libellé. Le simulateur
s'en sert pour vérifier que les GHM des tables existent, pour écrire les
libellés dans les sorties, et pour connaître la racine d'un GHM quand une
table n'a pas de ligne pour ce GHM (repli `ghm → racine_ghm`). Les colonnes
de regroupement de l'ATIH (`ga`, `gp`, `da`) ne servent à aucun tirage ni à
aucun repli dans la version 2 : elles sont conservées parce qu'elles
permettent de fabriquer des données fictives réalistes (choisir des racines
plausibles pour chaque spécialité) et d'analyser les sorties par groupe
d'activité.

---

## 3. Tables de type GHM

Calculables sur une base nationale ou sur plusieurs établissements : elles
ne dépendent pas de l'organisation d'un hôpital particulier.

### `niveaux.csv` — quel niveau de sévérité (C2)

| Colonne | Contenu |
| --- | --- |
| `racine_ghm` | racine |
| `classe_age` | 0-17, 18-64, 65-74, 75+ |
| `niveau` | 1 à 4, J, T, Z, A à E |
| `part` | proportion |

Bloc : `racine_ghm × classe_age`. Repli : `racine_ghm` seule.

**Calcul.** Tous les séjours (urgences et programmés confondus). Pour chaque
racine et classe d'âge, compter les séjours par niveau ; seuil et catégories
rares ; normaliser.

### `durees_ghm.csv` — combien de jours dure le séjour (C3)

| Colonne | Contenu |
| --- | --- |
| `ghm` | GHM complet |
| `classe_age_duree` | 0-59, 60+ |
| `duree_j` | 0 à 60, puis `60+` |
| `part` | proportion |
| `moyenne_60plus` | durée moyenne, en jours, des séjours de plus de 60 jours (renseignée sur la ligne `60+` seulement) |

Bloc : `ghm × classe_age_duree`. Repli : `ghm` seul, puis `racine_ghm`
(lignes avec `ghm` réduit à la racine).

**Calcul.** Durée = date de sortie moins date d'entrée, en jours entiers (0
pour une sortie le jour même). Pour chaque GHM et classe d'âge, compter les
séjours par durée de 0 à 60 ; regrouper au-delà de 60 en une classe `60+`
dont on note la durée moyenne ; seuil et catégories rares sur les classes de
durée ; normaliser. Le simulateur tire une durée en jours, puis les heures
d'entrée et de sortie fixent la durée exacte.

### `soins_critiques_ghm.csv` — passe-t-on par les soins critiques (C4)

| Colonne | Contenu |
| --- | --- |
| `ghm` | GHM complet |
| `classe_age_duree` | 0-59, 60+ |
| `part_rea` | part des séjours avec au moins un jour de réanimation |
| `part_usi` | idem soins intensifs |
| `part_usc` | idem surveillance continue |

Trois proportions indépendantes (chacune entre 0 et 1, elles ne somment pas
à 1). Repli : appliqué à la fabrication (voir calcul), pas par le simulateur.

**Calcul.** D'après le type d'autorisation des unités médicales (et non
d'après les suppléments du RSA, dont les règles d'attribution ne recoupent
pas la façon dont les services sont déclarés) : un séjour « passe » par un
type de soins critiques s'il a au moins un RUM dans une unité dont
l'autorisation appartient à la liste de ce type, donnée dans le bloc
`autorisations_soins_critiques` de `structure.yaml`. Cette définition est la
même que celle qui classe les services, ce qui garantit que les tables de
trajectoires et cette table sont cohérentes. Elle reste calculable sur la
base nationale, le RSA portant le type d'autorisation de chaque RUM. Pour
chaque GHM et classe d'âge, part des séjours concernés. Une part calculée sur moins de
`seuil_effectif` séjours est remplacée, dès la fabrication du fichier, par
celle du GHM sans distinction d'âge, puis par celle de la racine : le fichier
livré n'a qu'un niveau et le simulateur n'a pas de repli à faire.

### `sexe_racine.csv` — le sexe sachant la racine (C1)

| Colonne | Contenu |
| --- | --- |
| `racine_ghm` | racine |
| `sexe` | `F`, `M` |
| `part` | proportion |

Bloc : `racine_ghm`. Repli : 50/50.

**Calcul.** Tous les séjours, part de chaque sexe par racine. Sert surtout
aux entrées programmées (le sexe des patients des urgences est tiré avant la
racine, en A2 ; il n'est alors utilisé que comme garde-fou).

### `sorties.csv` — comment se termine le séjour (C7)

| Colonne | Contenu |
| --- | --- |
| `racine_ghm` | racine |
| `niveau` | niveau |
| `classe_age` | 0-17, 18-64, 65-74, 75+ |
| `mode_sortie` | domicile, SSR, EHPAD, transfert, deces |
| `part` | proportion |

Bloc : `racine_ghm × niveau × classe_age`. Repli : `racine_ghm × niveau`,
puis `racine_ghm`.

**Calcul.** Tous les séjours terminés. Mode de sortie et destination du RSS
traduits ainsi : 8 sans destination médico-sociale → domicile ; 8 avec
destination 7 → EHPAD ; 7 avec destination 2 → SSR ; 7 avec destination 1
ou autre → transfert ; 9 → deces. Les sorties par mutation interne (6) ne
sont pas des fins de séjour. Compter par bloc, seuil et catégories rares,
normaliser.

---

## 4. Tables de type spécialité

Dépendent de l'organisation : elles se calculent sur un établissement ou sur
un ensemble d'établissements comparables, à partir d'un tableau de passages
d'unités (RUM) où chaque unité a été ramenée à sa spécialité et où les
passages en UHCD ont été retirés.

### `premier_service_racine.csv` — dans quelle spécialité est pris en charge un patient de telle racine (A7, D)

| Colonne | Contenu |
| --- | --- |
| `racine_ghm` | racine |
| `classe_age` | 0-17, 18-64, 65-74, 75+ |
| `specialite` | une spécialité (hors UHCD) |
| `part` | proportion |
| `rang` | 1, 2 ou 3 |

Bloc : `racine_ghm × classe_age`. Repli : `racine_ghm` seule.

**Calcul.** *Tous* les séjours, quel que soit le mode d'entrée, et de
préférence sur plusieurs établissements : la question « dans quelle
spécialité un patient de telle racine est-il pris en charge » ne dépend pas
de la porte d'entrée, et les seuls séjours venus des urgences d'un seul site
ne donnent pas des effectifs suffisants. La condition est que la
correspondance unités → spécialités utilise les mêmes codes
(`specialites.csv`) sur tous les sites agrégés. Pour chaque séjour, le
premier service est la spécialité du premier RUM *autre que l'UHCD* ; les
séjours limités à l'UHCD sont exclus (situation `mono_uhcd` de `venues_urgences.csv`).
Pour chaque racine et classe d'âge, compter par premier service ; seuil et
catégories rares ; normaliser. Puis attribuer le rang : 1 si la part est au
moins `part_min_service_habituel` (0,10 par défaut) ; 2 si la ligne a été
observée au-dessus du seuil d'effectif mais que sa part est inférieure ; 3 si
la ligne vient de la répartition uniforme des catégories rares.

**Usage.** Au chargement, le simulateur restreint chaque bloc aux
spécialités présentes dans la structure et renormalise ; s'il ne reste aucun
service de rang 1 ni de rang 2 pour une racine, les patients de cette racine
venus des urgences sont transférés (l'hôpital n'a pas l'activité). Le
service prévu est ensuite tiré sur la distribution restreinte. En cas de
lits manquants, la redirection ne considère que les lignes de rang 1, puis
de rang 2, jamais celles de rang 3 (règle D).

### `casemix.csv` — quels patients en entrée programmée (B3)

| Colonne | Contenu |
| --- | --- |
| `specialite` | spécialité du service d'entrée |
| `classe_age` | 0-17, 18-64, 65-74, 75+ |
| `racine_ghm` | racine |
| `part` | proportion |

Bloc : `specialite` (les parts somment à 1 sur l'ensemble des couples
`classe_age × racine_ghm` de la spécialité). Pas de repli : chaque
spécialité de la structure doit avoir ses lignes.

**Calcul.** Séjours *non* venus des urgences. Le service d'entrée est la
spécialité du premier RUM. Pour chaque spécialité, compter les séjours par
couple (classe d'âge, racine) ; seuil et catégories rares ; normaliser.

### `table_passage.csv` — la suite du parcours, étape par étape (C5, D)

| Colonne | Contenu |
| --- | --- |
| `niveau` | `complet` ou `service` |
| `service` | spécialité courante |
| `precedent` | spécialité précédente, ou `AUCUN` pour le premier service (vide au niveau `service`) |
| `classe_age` | classe d'âge (vide au niveau `service`) |
| `mode_entree` | urgences, programme (vide au niveau `service`) |
| `suite` | spécialité suivante, ou `sortie` |
| `part` | proportion |
| `rang` | 1, 2 ou 3 (pour les lignes dont `suite` est une spécialité) |

Bloc : au niveau `complet`, `service × precedent × classe_age × mode_entree` ;
au niveau `service`, le service seul. Repli : le niveau `service`, pour une
case jamais observée au niveau complet.

**Rôle.** C'est la seule table de parcours : le parcours de chaque patient
se construit pas à pas, en tirant à chaque étape la suite sachant le service
où il est et celui d'où il vient, jusqu'à tirer `sortie`. Il n'y a pas de
catalogue de parcours complets dans cette version (voir la spécification,
pistes remises à plus tard).

**Calcul.** Sur tous les séjours. Pour chaque séjour, la suite des
spécialités de ses RUM (UHCD retirée), en fusionnant les RUM consécutifs de
même spécialité ; chaque étape donne une observation : (service, précédent,
classe d'âge, mode d'entrée) → suite, la dernière étape ayant pour suite
`sortie`. Compter par bloc aux deux niveaux, seuil et catégories rares,
normaliser. Le rang se calcule comme dans `premier_service_racine.csv`, par
bloc, sur les lignes dont la suite est une spécialité.

### `durees_passage.csv` — combien de temps dans chaque spécialité (B1, C6)

| Colonne | Contenu |
| --- | --- |
| `specialite` | spécialité |
| `mode_entree` | urgences, programme |
| `classe_age` | 0-17, 18-64, 65-74, 75+ |
| `mediane_j`, `q1_j`, `q3_j` | médiane et quartiles de la durée d'un passage, en jours (décimaux) |
| `moyenne_j` | durée moyenne, en jours |

Repli : `specialite` seule.

**Calcul.** Passages d'unités fusionnés par spécialité (un passage = une
suite de RUM consécutifs de même spécialité) ; durée = sortie moins entrée
du passage, en jours décimaux si les heures sont connues, sinon en jours
entiers. Par bloc, médiane, quartiles et moyenne, sur les blocs d'au moins
`seuil_effectif` passages.

**Usage.** `moyenne_j` sert à la loi de Little (B1) : entrées programmées du
mois = lits × occupation visée × jours du mois ÷ moyenne, moins les entrées
attendues par les urgences et par mutation. La médiane sert à répartir la
durée totale du séjour entre les étapes de la trajectoire (C6).

---

## 5. Profils des urgences et règles d'attente

### `profil_arrivees_urgences.csv` — quand arrivent les patients (A1)

| Colonne | Contenu |
| --- | --- |
| `dimension` | `heure`, `jour_semaine`, `mois` |
| `valeur` | 0 à 23 ; 1 (lundi) à 7 ; 1 à 12 |
| `part` | proportion |

Bloc : chaque dimension. Les trois profils sont multipliés entre eux ; le
volume vient de `passages_par_an`.

**Calcul.** RPU : part des arrivées par heure, par jour de la semaine, par
mois.

### `profil_patients_urgences.csv` — qui arrive (A2)

| Colonne | Contenu |
| --- | --- |
| `classe_age`, `sexe`, `part` | bloc : l'ensemble |

**Calcul.** RPU : part de chaque couple (classe d'âge, sexe).

### `venues_urgences.csv` — ce qui arrive à une venue aux urgences (A3)

| Colonne | Contenu |
| --- | --- |
| `periode` | mois, 1 à 12 |
| `classe_age` | 0-17, 18-64, 65-74, 75+ |
| `situation` | `consultation`, `hospitalisation`, `mono_uhcd`, `transfert`, `deces`, `parti` |
| `gravite` | CCMU 1 à 5, remplie pour `consultation`, `transfert`, `deces`, `parti` ; vide sinon |
| `racine_ghm` | racine du séjour, remplie pour `hospitalisation` et `mono_uhcd` ; vide sinon |
| `part` | proportion |

Bloc : `periode × classe_age` (les parts de toutes les lignes d'un mois et
d'une classe d'âge somment à 1). Repli : `classe_age` seule (lignes avec
`periode` vide).

**Le principe.** Chaque venue est caractérisée par sa situation et par ce
qui décrit le mieux sa gravité : la CCMU quand elle n'est pas hospitalisée,
la racine de GHM quand elle l'est. Ces deux informations sont tirées
ensemble avec la situation, parce qu'elles ne se déduisent pas l'une de
l'autre.

**Les situations.** `consultation` : retour à domicile sans séjour ;
`hospitalisation` : séjour comportant au moins un passage dans un service
autre que l'UHCD ; `mono_uhcd` : séjour dont tous les RUM sont dans l'unité
d'UHCD ; `transfert` : vers un autre établissement ; `deces` ; `parti` :
parti sans attendre ou contre avis.

**Calcul.** RPU chaînés aux séjours PMSI (un ou plusieurs établissements où
ce chaînage existe). Pour chaque venue, la situation d'après l'existence
d'un séjour chaîné et ses RUM (UHCD seule ou non), puis, à défaut, d'après
le mode de sortie du RPU ; la racine d'après le GHM du séjour ; la gravité
d'après la CCMU du RPU (P et D rattachées à 1 et 5). Pour chaque mois
d'arrivée et classe d'âge, compter par (situation, gravité, racine) ; seuil
et catégories rares ; normaliser. Sur plusieurs années, le mois est celui de
l'arrivée quelle que soit l'année : la table décrit la saisonnalité.

**Usage.** Un seul tirage donne la situation et sa caractérisation. La
durée du passage (A4) est tirée ensuite sachant la situation et, pour les
non hospitalisés, la gravité ; pour les hospitalisés, la gravité du RPU est
tirée en dernier (A5) ; le premier service (A7) est tiré sachant la racine.

### `gravite_urgences.csv` — la gravité CCMU des hospitalisés (A5)

| Colonne | Contenu |
| --- | --- |
| `classe_age` | 0-17, 18-64, 65-74, 75+ |
| `situation` | `hospitalisation`, `mono_uhcd` seulement |
| `tranche_h` | tranche de durée du passage (0-1, 1-2, 2-4, 4-6, 6-12, 12-24, 24+) |
| `gravite` | CCMU 1 à 5 |
| `part` | proportion |

Bloc : `classe_age × situation × tranche_h`. Repli : `classe_age × situation`
(lignes avec `tranche_h` vide).

**Calcul.** RPU des venues hospitalisées (séjour chaîné), champ CCMU, part
de chaque gravité par classe d'âge, situation et tranche de durée de
passage.

**Usage.** Pour les hospitalisés, c'est le GHM qui porte l'information de
gravité ; la CCMU n'est qu'un attribut du RPU produit, tiré en dernier
sachant la durée pour rester cohérent avec elle. Elle n'influence aucun
autre tirage.

### `classes_tension.csv` — les bornes de la tension du jour (D1)

| Colonne | Contenu |
| --- | --- |
| `classe` | basse, moyenne, haute |
| `borne_inf`, `borne_sup` | bornes de la part des lits occupés par des patients venus des urgences (borne basse incluse ; `borne_inf` vide pour la première classe, `borne_sup` vide pour la dernière) |

**Définition de la tension.** Chaque jour à `heure_releve_tension`, nombre
de patients présents dont le séjour est venu des urgences (UHCD comprise),
divisé par le nombre total de lits installés (tous services sauf les
urgences). Dans le simulateur, elle est calculée sur l'état simulé ; sur les
données réelles, sur l'historique des mouvements (ou, à défaut, sur les RUM
au jour).

**Calcul des bornes.** Série quotidienne de la tension sur l'historique ;
les deux bornes sont les terciles (33e et 67e centiles). D'autres bornes sont
possibles (seuils de l'établissement) ; ce qui compte est qu'elles soient
les mêmes pour estimer les tables et pour simuler.

### `durees_passage_urgences.csv` — combien de temps aux urgences (A4)

| Colonne | Contenu |
| --- | --- |
| `situation` | consultation, hospitalisation, mono_uhcd, transfert, deces, parti |
| `gravite` | CCMU 1 à 5 pour les situations sans séjour ; vide pour hospitalisation et mono_uhcd |
| `classe_age` | 0-17, 18-64, 65-74, 75+ |
| `classe_tension` | basse, moyenne, haute |
| `tranche_h` | 0-1, 1-2, 2-4, 4-6, 6-12, 12-24, 24+ |
| `part` | proportion |

Bloc : `situation × gravite × classe_age × classe_tension`. Repli :
`situation × gravite × classe_age` (lignes avec `classe_tension` vide), puis
`situation × classe_age` (gravité vide).

**Calcul.** RPU : durée = sortie moins arrivée, en heures, *UHCD comprise*
(la sortie du RPU est la sortie de la structure des urgences, UHCD incluse ;
à vérifier selon la façon dont les RPU sont produits). Chaque passage reçoit
la classe de tension du jour de son arrivée, et, pour les situations sans
séjour, sa CCMU. Pour la situation `mono_uhcd`, la durée est celle de
l'ensemble urgences + UHCD et sert de durée de séjour (plancher 6 h au
tirage). Pour la situation `hospitalisation`, ne garder que les passages
dont le premier service du séjour est de rang 1 dans
`premier_service_racine.csv` : c'est la durée « sans redirection » ; la
redirection est portée par la table suivante. Compter par tranche, seuil et
catégories rares, normaliser. Le simulateur tire une tranche, puis une
valeur uniforme dans la tranche (pour `24+`, uniforme entre 24 et 48 h).

### `supplement_rang2.csv` — l'attente supplémentaire avant un service exceptionnel (D4)

| Colonne | Contenu |
| --- | --- |
| `classe_tension` | basse, moyenne, haute |
| `tranche_h` | 0-1, 1-2, 2-4, 4-6, 6-12, 12-24, 24+ |
| `part` | proportion |

Bloc : `classe_tension`. Repli : l'ensemble (ligne avec `classe_tension`
vide).

**Calcul.** Passages aux urgences suivis d'une hospitalisation dont le
premier service est de rang 2. Pour chacun : durée de passage moins la
durée *médiane* des passages de rang 1 de la même case (situation
hospitalisation, classe d'âge, classe de tension) ; les écarts négatifs sont
ramenés à zéro. Ranger les écarts par tranche, par classe de tension ;
normaliser. On ne distingue pas l'âge pour garder des effectifs suffisants.

**Usage.** Quand un patient ne trouve de lit ni dans son service prévu ni
dans un service de rang 1, il tire un supplément dans cette table, attend ce
temps (en prenant un lit de rang 1 s'il s'en libère un), puis prend un lit
de rang 2. Estimer la table précédente sur le rang 1 et celle-ci sur l'écart
rang 2 − rang 1 évite de compter deux fois la même attente.

### `profil_entrees_programmees.csv` — quand entrent les programmés (B2)

| Colonne | Contenu |
| --- | --- |
| `dimension` | `jour_semaine`, `heure` |
| `valeur`, `part` | bloc : chaque dimension |

**Calcul.** Séjours non venus des urgences : part des entrées par jour de la
semaine et par heure (mouvements, ou RUM si l'heure est connue).

### `profil_sorties.csv` — à quelle heure sort-on (C8)

| Colonne | Contenu |
| --- | --- |
| `heure`, `part` | bloc : l'ensemble |

**Calcul.** Mouvements de sortie : part par heure. L'effet du week-end
est dans la structure (`facteur_weekend`).

---

## 6. Fichier de contrôle (pas un paramètre)

### `controle/cibles.csv` — les fourchettes attendues

| Colonne | Contenu |
| --- | --- |
| `indicateur` | nom de l'indicateur du tableau de contrôle |
| `service` | service, ou vide pour l'hôpital entier |
| `classe_tension` | classe, ou vide |
| `min`, `max` | fourchette acceptée |

Le simulateur ne lit pas ce fichier pour simuler ; il sert au tableau de
contrôle (`controle.md`) qui compare le jeu produit aux attentes. Les
fourchettes viennent des agrégats de référence quand ils existent (taux
d'occupation, durées, part des séjours venus des urgences, nombre de RUM par
séjour) et de choix d'expert sinon (part des premiers services de rang 2,
lits brancards, occupation de l'UHCD, attente médiane, transferts faute de
lit, reports d'entrées programmées), de préférence par classe de tension.

---

## 7. Récapitulatif : quel fichier pour quelle étape

| Étape | Ce qui est tiré ou décidé | Fichier |
| --- | --- | --- |
| A1 | instant d'arrivée aux urgences | `structure.yaml` (passages_par_an), `profil_arrivees_urgences.csv` |
| A2 | classe d'âge, sexe | `profil_patients_urgences.csv` |
| A3 | situation de la venue, avec sa CCMU (sans séjour) ou sa racine de GHM (avec séjour) | `venues_urgences.csv` |
| A4 | durée du passage, sachant situation et gravité | `durees_passage_urgences.csv` (+ tension du jour) |
| A5 | gravité CCMU des hospitalisés, sachant situation et tranche de durée | `gravite_urgences.csv` |
| A6 | lit d'UHCD à la 6e heure (situations hospitalisation et mono_uhcd) | `structure.yaml` (seuil_uhcd_h) |
| A7 | premier service (situation hospitalisation) | `premier_service_racine.csv` |
| B1 | nombre d'entrées programmées par jour et service | `structure.yaml` (lits, occupation_visee), `durees_passage.csv`, tables A3 et A7 |
| B2 | jour et heure d'entrée | `profil_entrees_programmees.csv` |
| B3 | classe d'âge et racine | `casemix.csv` |
| B4 | report au lendemain | `structure.yaml` (delai_report_programme_h) |
| C1 | sexe | `sexe_racine.csv` |
| C2 | niveau, donc GHM | `niveaux.csv` |
| C3 | durée totale du séjour | `durees_ghm.csv` |
| C4 | soins critiques | `soins_critiques_ghm.csv` |
| C5 | trajectoire, étape par étape | `table_passage.csv` |
| C6 | durée par étape | `durees_passage.csv` |
| C7 | mode de sortie | `sorties.csv` |
| C8 | heures de sortie, effet week-end | `profil_sorties.csv`, `structure.yaml` |
| D1 | tension du jour | `classes_tension.csv`, `structure.yaml` (heure_releve_tension) |
| D2–D6 | service obtenu, rang, attentes | `premier_service_racine.csv`, `table_passage.csv` (rang), `supplement_rang2.csv`, `structure.yaml` (priorités, soins critiques) |
| D7 | transfert, report | `structure.yaml` (delai_transfert_h, delai_report_programme_h) |
| E | retours de patients connus | `structure.yaml` (probabilite_retour) |
