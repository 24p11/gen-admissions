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
lignes complétées reçoivent le rang 0.

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
| `classe_age` | `0-17`, `18-64`, `65-79`, `80+` | toutes les tables par racine de GHM, par spécialité, et les profils des urgences |
| `classe_age_duree` | `0-59`, `60+` | les tables par GHM complet (`durees_ghm`, `soins_critiques_ghm`), où le croisement avec 2 639 GHM impose des classes larges |

Les bornes sont des âges révolus à l'entrée.

**Vocabulaire des codes.**

| Colonne | Valeurs | Sens |
| --- | --- | --- |
| `mode_entree` | `urgences`, `programme` | séjour venu des urgences (provenance 5 du RSS) ou non |
| `issue` (passage aux urgences) | `domicile`, `hospitalisation`, `transfert`, `deces`, `parti` | hospitalisation = sur place, UHCD seule comprise ; transfert = vers un autre établissement ; parti = parti sans attendre |
| `mode_sortie` (séjour) | `domicile`, `SSR`, `EHPAD`, `transfert`, `deces` | codes PMSI 8, 7-2, 8-7, 7-1, 9 |
| `niveau` | `1`, `2`, `3`, `4`, `J`, `T`, `Z`, `A`, `B`, `C`, `D`, `E` | sixième caractère du GHM |
| `periode` | `1` à `12` | mois civil |
| `classe_tension` | `basse`, `moyenne`, `haute` | voir `classes_tension.csv` |
| `tranche_h` | `0-1`, `1-2`, `2-4`, `4-6`, `6-12`, `12-24`, `24+` | tranches de durée en heures, borne basse incluse |
| `duree_j` | `0` à `60`, puis `60+` | durée de séjour en jours entiers (date de sortie moins date d'entrée) |
| `rang` | `0`, `1`, `2` | 0 complété par la règle des catégories rares ; 1 service habituel ; 2 service exceptionnel |

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
traitée comme un prolongement des urgences, jamais comme une étape du parcours. Dans toutes
les tables de parcours (premier service, catalogue, table de passage), les
passages en UHCD sont retirés avant calcul : le premier service d'un séjour
est le premier service *autre que l'UHCD*. Un séjour qui se limite à l'UHCD
est codé par la modalité `MONO_UHCD` du premier service.

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
classes_age: [0-17, 18-64, 65-79, 80+]
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
  seuil_uhcd_h: 6                      # durée de passage au-delà de laquelle un lit d'UHCD est demandé (A5)
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
    65-79: 0.20
    80+: 0.28

simulation:
  duree_jours: 365
  chauffe_jours: 42
  marge_fin_jours: 60
  graine: 42
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
structure. La modalité `MONO_UHCD` n'est pas une spécialité : elle n'apparaît
que dans `premier_service_racine.csv`.

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

### `casemix_urgences.csv` — quelle racine pour un patient hospitalisé depuis les urgences (A6)

| Colonne | Contenu |
| --- | --- |
| `periode` | mois, 1 à 12 |
| `classe_age` | 0-17, 18-64, 65-79, 80+ |
| `racine_ghm` | racine |
| `part` | proportion |

Bloc : `periode × classe_age`. Repli : `classe_age` seule (lignes avec
`periode` vide).

**Calcul.** Prendre tous les séjours dont le mode d'entrée indique les
urgences (provenance 5), *y compris* les séjours limités à l'UHCD. Pour
chaque mois d'entrée et classe d'âge, compter les séjours par racine de
GHM ; appliquer le seuil et la règle des catégories rares ; diviser par le
total du bloc. Sur cinq années, le mois est celui de l'entrée quelle que
soit l'année : la table décrit la saisonnalité, pas une année précise.

### `niveaux.csv` — quel niveau de sévérité (C2)

| Colonne | Contenu |
| --- | --- |
| `racine_ghm` | racine |
| `classe_age` | 0-17, 18-64, 65-79, 80+ |
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

**Calcul.** D'après les suppléments de réanimation, de soins intensifs et de
surveillance continue du RSA : un séjour « passe » par un type de soins
critiques s'il a au moins un supplément de ce type (réanimation pédiatrique
avec la réanimation, néonatologie avec les soins intensifs). Pour chaque GHM
et classe d'âge, part des séjours concernés. Une part calculée sur moins de
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
| `classe_age` | 0-17, 18-64, 65-79, 80+ |
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

### `premier_service_racine.csv` — dans quelle spécialité entre un patient venu des urgences (A7, D)

| Colonne | Contenu |
| --- | --- |
| `racine_ghm` | racine |
| `classe_age` | 0-17, 18-64, 65-79, 80+ |
| `specialite` | une spécialité (hors UHCD) ou `MONO_UHCD` |
| `part` | proportion |
| `rang` | 0, 1 ou 2 |

Bloc : `racine_ghm × classe_age`. Repli : `racine_ghm` seule.

**Calcul.** Séjours venus des urgences. Pour chacun, le premier service est
la spécialité du premier RUM *autre que l'UHCD* ; s'il n'y en a pas (séjour
limité à l'UHCD), le premier service vaut `MONO_UHCD`. Pour chaque racine et
classe d'âge, compter par premier service ; seuil et catégories rares ;
normaliser. Puis attribuer le rang : 1 si la part est au moins
`part_min_service_habituel` (0,10 par défaut) ; 2 si la ligne a été
observée au-dessus du seuil d'effectif mais que sa part est inférieure ; 0 si
la ligne vient de la répartition uniforme des catégories rares. `MONO_UHCD`
reçoit toujours le rang 1 (il n'y a pas de redirection vers l'UHCD).

**Usage.** Le service prévu est tiré sur toute la distribution. En cas de
lits manquants, la redirection ne considère que les lignes de rang 1, puis
de rang 2, jamais celles de rang 0 (règle D).

### `casemix.csv` — quels patients en entrée programmée (B3)

| Colonne | Contenu |
| --- | --- |
| `specialite` | spécialité du service d'entrée |
| `classe_age` | 0-17, 18-64, 65-79, 80+ |
| `racine_ghm` | racine |
| `part` | proportion |

Bloc : `specialite` (les parts somment à 1 sur l'ensemble des couples
`classe_age × racine_ghm` de la spécialité). Pas de repli : chaque
spécialité de la structure doit avoir ses lignes.

**Calcul.** Séjours *non* venus des urgences. Le service d'entrée est la
spécialité du premier RUM. Pour chaque spécialité, compter les séjours par
couple (classe d'âge, racine) ; seuil et catégories rares ; normaliser.

### `catalogue_trajectoires.csv` — les parcours complets fréquents (C5)

| Colonne | Contenu |
| --- | --- |
| `trajectoire` | suite de spécialités séparées par ` > ` (`CARDIO > REA > CARDIO`), sans l'UHCD |
| `mode_entree` | urgences, programme |
| `classe_age` | 0-17, 18-64, 65-79, 80+ |
| `part` | proportion |

Bloc : `mode_entree × classe_age × premier service de la trajectoire`. Le
complément à 1 dans un bloc est la part des séjours *hors catalogue*, qui
suivront la table de passage.

**Calcul.** Pour chaque séjour, la trajectoire est la suite des spécialités
de ses RUM (UHCD retirée), en fusionnant les RUM consécutifs de même
spécialité. Pour chaque bloc, compter les séjours par trajectoire ; ne
retenir que les trajectoires d'effectif au moins égal au seuil ; diviser
par l'effectif *total* du bloc (hors catalogue compris), de sorte que la
somme des parts du bloc soit inférieure à 1.

### `table_passage.csv` — la suite du parcours, étape par étape (C5, D)

| Colonne | Contenu |
| --- | --- |
| `niveau` | `complet`, `sans_precedent`, `sans_age`, `service` |
| `service` | spécialité courante |
| `precedent` | spécialité précédente, ou `AUCUN` pour le premier service (vide aux niveaux qui l'ignorent) |
| `classe_age` | classe d'âge (vide aux niveaux qui l'ignorent) |
| `mode_entree` | urgences, programme (vide au niveau `service`) |
| `suite` | spécialité suivante, ou `sortie` |
| `part` | proportion |
| `rang` | 0, 1 ou 2 (pour les lignes dont `suite` est une spécialité) |

Bloc : les colonnes de contexte du niveau. Repli : le niveau suivant, du
plus détaillé au plus général.

**Calcul.** Sur les seuls séjours *non couverts* par le catalogue (sinon les
parcours rares, qui passent par exemple en réanimation, seraient
sous-représentés). Chaque étape d'un séjour donne une observation :
(service, précédent, classe d'âge, mode d'entrée) → suite. Compter par bloc
à chaque niveau, seuil et catégories rares, normaliser. Le rang se calcule
comme dans `premier_service_racine.csv`, par bloc, sur les lignes dont la
suite est une spécialité.

### `durees_passage.csv` — combien de temps dans chaque spécialité (B1, C6)

| Colonne | Contenu |
| --- | --- |
| `specialite` | spécialité |
| `mode_entree` | urgences, programme |
| `classe_age` | 0-17, 18-64, 65-79, 80+ |
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

### `gravite_urgences.csv` — la gravité CCMU (A2)

| Colonne | Contenu |
| --- | --- |
| `classe_age`, `gravite` (1 à 5), `part` | bloc : `classe_age` |

**Calcul.** RPU, champ CCMU, part par classe d'âge. Les CCMU P et D sont
rattachées respectivement à 1 et à 5.

### `issues_urgences.csv` — comment se termine le passage (A3)

| Colonne | Contenu |
| --- | --- |
| `classe_age`, `gravite`, `issue`, `part` | bloc : `classe_age × gravite` |

Repli : `classe_age` seule.

**Calcul.** RPU : `hospitalisation` si le mode de sortie est une
hospitalisation sur place (UHCD comprise) ; `transfert` si mutation ou
transfert vers un autre établissement ; `deces` ; `parti` si parti sans
attendre ou contre avis ; `domicile` sinon.

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
| `issue` | domicile, hospitalisation, transfert, deces, parti |
| `classe_age` | 0-17, 18-64, 65-79, 80+ |
| `classe_tension` | basse, moyenne, haute |
| `tranche_h` | 0-1, 1-2, 2-4, 4-6, 6-12, 12-24, 24+ |
| `part` | proportion |

Bloc : `issue × classe_age × classe_tension`. Repli : `issue × classe_age`
(lignes avec `classe_tension` vide).

**Calcul.** RPU : durée = sortie moins arrivée, en heures, *UHCD comprise*
(la sortie du RPU est la sortie de la structure des urgences, UHCD incluse ;
à vérifier selon la façon dont les RPU sont produits). Chaque passage reçoit
la classe de tension du jour de son arrivée. Pour l'issue
`hospitalisation`, ne garder que les passages dont le premier service du
séjour est de rang 1 dans `premier_service_racine.csv` (ou `MONO_UHCD`) :
c'est la durée « sans redirection » ; la redirection est portée par la table
suivante. Compter par tranche, seuil et catégories rares, normaliser. Le
simulateur tire une tranche, puis une valeur uniforme dans la tranche (pour
`24+`, uniforme entre 24 et 48 h).

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
durée *médiane* des passages de rang 1 de la même case (issue
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
| A2 | classe d'âge, sexe, gravité | `profil_patients_urgences.csv`, `gravite_urgences.csv` |
| A3 | issue du passage | `issues_urgences.csv` |
| A4 | durée du passage | `durees_passage_urgences.csv` (+ tension du jour) |
| A5 | lit d'UHCD au-delà de 6 h | `structure.yaml` (seuil_uhcd_h) |
| A6 | racine de GHM | `casemix_urgences.csv` |
| A7 | premier service, ou MONO_UHCD | `premier_service_racine.csv` |
| B1 | nombre d'entrées programmées par jour et service | `structure.yaml` (lits, occupation_visee), `durees_passage.csv`, tables A |
| B2 | jour et heure d'entrée | `profil_entrees_programmees.csv` |
| B3 | classe d'âge et racine | `casemix.csv` |
| B4 | report au lendemain | `structure.yaml` (delai_report_programme_h) |
| C1 | sexe | `sexe_racine.csv` |
| C2 | niveau, donc GHM | `niveaux.csv` |
| C3 | durée totale du séjour | `durees_ghm.csv` |
| C4 | soins critiques | `soins_critiques_ghm.csv` |
| C5 | trajectoire | `catalogue_trajectoires.csv`, `table_passage.csv` |
| C6 | durée par étape | `durees_passage.csv` |
| C7 | mode de sortie | `sorties.csv` |
| C8 | heures de sortie, effet week-end | `profil_sorties.csv`, `structure.yaml` |
| D1 | tension du jour | `classes_tension.csv`, `structure.yaml` (heure_releve_tension) |
| D2–D6 | service obtenu, rang, attentes | `premier_service_racine.csv`, `table_passage.csv` (rang), `supplement_rang2.csv`, `structure.yaml` (priorités, soins critiques) |
| D7 | transfert, report | `structure.yaml` (delai_transfert_h, delai_report_programme_h) |
| E | retours de patients connus | `structure.yaml` (probabilite_retour) |
