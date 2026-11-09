"""Chargement, vérification et préparation des 22 fichiers de paramètres.

C'est la porte d'entrée du simulateur : tout ce que les autres modules savent
de l'hôpital passe par ``Parametres.charger(dossier)``. Le module fait trois
choses, et rien d'autre (pas de temps, pas de lits, pas de patients) :

1. **lire et vérifier** : les fichiers du dictionnaire (``parametres/DICTIONNAIRE.md``)
   sont lus en texte (les cases vides des lignes de repli restent des chaînes
   vides), puis ``verifier_dossier`` refuse un dossier incomplet ou incohérent ;
2. **appliquer ce qui dépend de la structure** : les deux tables de pratiques
   calculées sur plusieurs établissements (``premier_service_racine``,
   ``table_passage``) sont restreintes aux services de ``structure.yaml``,
   renormalisées, et leurs rangs recalculés ; une racine sans service d'accueil
   conventionnel est marquée à transférer ;
3. **préparer les tirages** : chaque table de proportions devient une
   ``TableTirage`` — pour chaque niveau (fin, puis replis), un dictionnaire
   « clé du bloc → catégories et parts cumulées », de sorte qu'un tirage soit
   une recherche dichotomique et que la recherche de repli soit automatique.

Exemple::

    p = Parametres.charger("parametres/hopital_type")
    p.venues.tirer(rng, saison="2024-2025", periode=1, classe_age="75+")
    # -> ('hospitalisation', '', '05M09')
    p.premier_service.chercher(racine_ghm="05M09", classe_age="75+").categories
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
import yaml

from gen_admissions.preparation_parametres import attribuer_rang, restreindre_services, verifier_dossier

TYPES_CONVENTIONNELS = ("medecine", "chirurgie", "obstetrique", "pediatrie")
JOURS_SEMAINE = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


# ---------------------------------------------------------------------------
# Lecture tolérante
# ---------------------------------------------------------------------------

def texte(x: Any) -> str:
    """Valeur de bloc en texte propre : 1, 1.0, « 1.0 » → « 1 » ; NaN → « »."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).strip()
    if s in ("nan", "<NA>", "None"):
        return ""
    if s.endswith(".0") and s[:-2].lstrip("-").isdigit():
        return s[:-2]
    return s


def lire_csv(chemin: Path, nombres: Sequence[str] = ("part",)) -> pd.DataFrame:
    """Lit un fichier du dictionnaire : tout en texte, puis les colonnes ``nombres`` en flottants."""
    t = pd.read_csv(chemin, dtype=str, keep_default_na=False)
    for c in t.columns:
        if c in nombres:
            t[c] = pd.to_numeric(t[c].replace("", np.nan))
        else:
            t[c] = t[c].map(texte)
    return t


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Service:
    code: str
    libelle: str
    specialite: str
    type: str
    lits: int
    lits_ouverts: int | None = None                    # la capacité réellement utilisée par le simulateur, si renseignée
    soins_critiques: frozenset[str] = frozenset()      # parmi rea, usi, usc
    presents_vises: float | None = None
    age_min: int | None = None
    age_max: int | None = None
    sexe: str | None = None

    @property
    def conventionnel(self) -> bool:
        return self.type in TYPES_CONVENTIONNELS

    @property
    def capacite(self) -> int:
        """Les lits que le simulateur peut remplir : ``lits_ouverts`` s'il est renseigné, sinon ``lits``."""
        return int(self.lits_ouverts) if self.lits_ouverts else int(self.lits)

    def accepte(self, classe_age: str, sexe: str | None = None) -> bool:
        """Le service accepte-t-il un patient de cette classe d'âge (« 0-17 », « 75+ ») et de ce sexe ?"""
        if self.sexe and sexe and sexe != self.sexe:
            return False
        bas, haut = _bornes_classe(classe_age)
        if self.age_min is not None and haut is not None and haut < self.age_min:
            return False
        if self.age_max is not None and bas > self.age_max:
            return False
        return True


def _bornes_classe(classe_age: str) -> tuple[int, int | None]:
    """« 65-74 » → (65, 74) ; « 75+ » → (75, None)."""
    c = classe_age.strip()
    if c.endswith("+"):
        return int(c[:-1]), None
    a, b = c.split("-")
    return int(a), int(b)


@dataclass
class Structure:
    etablissement: str
    description: str
    debut: pd.Timestamp
    classes_age: list[str]
    classes_age_duree: list[str]
    services: dict[str, Service]
    urgences: dict[str, Any]
    lits: dict[str, Any]
    sorties: dict[str, Any]
    population: dict[str, Any]
    simulation: dict[str, Any]
    autorisations_soins_critiques: dict[str, list[str]]

    @classmethod
    def depuis_yaml(cls, chemin: Path, types: dict[str, str]) -> "Structure":
        d = yaml.safe_load(open(chemin, encoding="utf-8"))
        services = {}
        for code, b in d["services"].items():
            sc = b.get("soins_critiques") or []
            if isinstance(sc, str):
                sc = [x.strip() for x in sc.split(",") if x.strip()]
            specialite = b.get("specialite", code)
            type_ = types.get(specialite, "soins_critiques" if sc else "medecine")
            if code == d.get("urgences", {}).get("service"):
                type_ = "urgences"
            services[code] = Service(
                code=code, libelle=b.get("libelle", code), specialite=specialite, type=type_, lits=int(b.get("lits", 0)),
                lits_ouverts=None if b.get("lits_ouverts") is None else int(round(float(b["lits_ouverts"]))),
                soins_critiques=frozenset(sc),
                presents_vises=None if b.get("presents_vises") is None else float(b["presents_vises"]),
                age_min=None if b.get("age_min") is None else int(b["age_min"]),
                age_max=None if b.get("age_max") is None else int(b["age_max"]),
                sexe=b.get("sexe"),
            )
        return cls(
            etablissement=d.get("etablissement", ""), description=d.get("description", ""),
            debut=pd.Timestamp(d.get("debut", "2026-01-01")),
            classes_age=[texte(x) for x in d.get("classes_age", [])], classes_age_duree=[texte(x) for x in d.get("classes_age_duree", [])],
            services=services, urgences=d.get("urgences", {}), lits=d.get("lits", {}), sorties=d.get("sorties", {}),
            population=d.get("population", {}), simulation=d.get("simulation", {}),
            autorisations_soins_critiques=d.get("autorisations_soins_critiques", {}),
        )

    @property
    def conventionnels(self) -> list[Service]:
        return [s for s in self.services.values() if s.conventionnel]

    @property
    def soins_critiques(self) -> list[Service]:
        return [s for s in self.services.values() if s.soins_critiques]

    @property
    def service_urgences(self) -> Service:
        return self.services[self.urgences["service"]]

    def services_pour(self, type_soins_critiques: str) -> list[Service]:
        """Les services qui couvrent un type de soins critiques (« rea », « usi », « usc »)."""
        return [s for s in self.services.values() if type_soins_critiques in s.soins_critiques]


# ---------------------------------------------------------------------------
# Tables à niveaux de repli
# ---------------------------------------------------------------------------

@dataclass
class Ligne:
    """Le contenu d'un bloc : ses catégories, leurs parts (et parts cumulées), ses colonnes annexes."""
    categories: list[tuple]            # une entrée par ligne du bloc ; tuple des colonnes de catégorie
    parts: np.ndarray
    cumul: np.ndarray
    annexes: dict[str, np.ndarray] = field(default_factory=dict)   # par exemple rang
    niveau: tuple[str, ...] = ()       # les colonnes de bloc du niveau où la ligne a été trouvée

    def tirer(self, rng: np.random.Generator) -> tuple:
        i = int(np.searchsorted(self.cumul, rng.random(), side="right"))
        return self.categories[min(i, len(self.categories) - 1)]

    def tableau(self) -> pd.DataFrame:
        t = pd.DataFrame(self.categories)
        t["part"] = self.parts
        for k, v in self.annexes.items():
            t[k] = v
        return t


class TableRepli:
    """Une table du dictionnaire organisée par niveaux : le niveau fin (toutes les colonnes de
    bloc remplies), puis les replis (certaines colonnes vides). ``chercher`` parcourt les niveaux
    du plus fin au plus grossier avec la clé fournie et renvoie les lignes du premier bloc trouvé."""

    def __init__(self, table: pd.DataFrame, colonnes_bloc: Sequence[str]):
        self.colonnes_bloc = list(colonnes_bloc)
        self.table = table.copy()
        for c in self.colonnes_bloc:
            self.table[c] = self.table[c].map(texte)
        if self.colonnes_bloc:
            remplies = self.table[self.colonnes_bloc].ne("")
            motifs = remplies.apply(lambda r: tuple(c for c in self.colonnes_bloc if r[c]), axis=1)
        else:                                   # table sans bloc (profil des patients) : un seul niveau, vide
            motifs = pd.Series([()] * len(self.table), index=self.table.index, dtype=object)
        self.niveaux: list[tuple[str, ...]] = sorted(set(motifs), key=lambda m: -len(m))
        self._blocs: dict[tuple[str, ...], dict[tuple, pd.DataFrame]] = {}
        for niveau in self.niveaux:
            sous = self.table[motifs == niveau]
            cles = [sous[c] for c in niveau] if niveau else []
            self._blocs[niveau] = {k if isinstance(k, tuple) else (k,): g for k, g in sous.groupby(cles)} if niveau else {(): sous}

    def chercher_bloc(self, **cle) -> tuple[pd.DataFrame, tuple[str, ...]] | None:
        cle = {k: texte(v) for k, v in cle.items()}
        for niveau in self.niveaux:
            if all(c in cle for c in niveau):
                g = self._blocs[niveau].get(tuple(cle[c] for c in niveau))
                if g is not None and len(g):
                    return g, niveau
        return None

    def __len__(self) -> int:
        return len(self.table)


class TableTirage(TableRepli):
    """Une table de proportions prête au tirage : à chaque bloc de chaque niveau, une ``Ligne``."""

    def __init__(self, table: pd.DataFrame, colonnes_bloc: Sequence[str], colonnes_categorie: Sequence[str],
                 annexes: Sequence[str] = ()):
        super().__init__(table, colonnes_bloc)
        self.colonnes_categorie = list(colonnes_categorie)
        self._lignes: dict[tuple[str, ...], dict[tuple, Ligne]] = {}
        for niveau, blocs in self._blocs.items():
            self._lignes[niveau] = {k: self._ligne(g, niveau, annexes) for k, g in blocs.items()}

    def _ligne(self, g: pd.DataFrame, niveau: tuple[str, ...], annexes: Sequence[str]) -> Ligne:
        g = g[g["part"] > 0]
        parts = g["part"].to_numpy(dtype=float)
        parts = parts / parts.sum()
        return Ligne(categories=[tuple(texte(x) for x in r) for r in g[self.colonnes_categorie].itertuples(index=False)],
                     parts=parts, cumul=np.cumsum(parts), annexes={a: g[a].to_numpy() for a in annexes if a in g.columns},
                     niveau=niveau)

    def chercher(self, **cle) -> Ligne | None:
        cle = {k: texte(v) for k, v in cle.items()}
        for niveau in self.niveaux:
            if all(c in cle for c in niveau):
                ligne = self._lignes[niveau].get(tuple(cle[c] for c in niveau))
                if ligne is not None:
                    return ligne
        return None

    def tirer(self, rng: np.random.Generator, **cle) -> tuple | str:
        """Tire une catégorie ; une seule colonne de catégorie → la valeur, plusieurs → le tuple."""
        ligne = self.chercher(**cle)
        if ligne is None:
            raise KeyError(f"aucun bloc pour {cle} (niveaux : {self.niveaux})")
        c = ligne.tirer(rng)
        return c[0] if len(self.colonnes_categorie) == 1 else c

    def blocs(self, niveau: tuple[str, ...] | None = None) -> list[tuple]:
        niveau = self.niveaux[0] if niveau is None else niveau
        return list(self._lignes[niveau])


# ---------------------------------------------------------------------------
# Objets simples : profils, durées de passage, soins critiques, tension
# ---------------------------------------------------------------------------

class ProfilSemaine:
    """Les 168 poids jour × heure d'un profil (arrivées, entrées programmées, sorties)."""

    def __init__(self, table: pd.DataFrame):
        t = table[table["dimension"] == "semaine"].copy()
        self.poids = np.zeros((7, 24))
        for _, r in t.iterrows():
            self.poids[int(texte(r["jour_semaine"])) - 1, int(texte(r["heure"]))] = float(r["part"])
        self.poids /= self.poids.sum() if self.poids.sum() else 1.0
        self._par_jour = self.poids.sum(axis=1)

    def part_jour(self, jour_semaine: int) -> float:
        """Part de la semaine qui tombe ce jour (1 = lundi … 7 = dimanche)."""
        return float(self._par_jour[jour_semaine - 1])

    def tirer_heure(self, rng: np.random.Generator, jour_semaine: int) -> float:
        """Heure décimale dans la journée, sachant le jour de semaine."""
        p = self.poids[jour_semaine - 1]
        if p.sum() == 0:
            return float(rng.random() * 24)
        h = int(rng.choice(24, p=p / p.sum()))
        return h + float(rng.random())


class ProfilMois:
    def __init__(self, table: pd.DataFrame):
        t = table[table["dimension"] == "mois"]
        self.poids = np.full(12, 1 / 12)
        for _, r in t.iterrows():
            self.poids[int(texte(r["mois"])) - 1] = float(r["part"])
        self.poids /= self.poids.sum() if self.poids.sum() else 1.0

    def facteur(self, mois: int) -> float:
        """Rapport au mois moyen (1 = un mois comme les autres)."""
        return float(self.poids[mois - 1] * 12)


class DureesPassage(TableRepli):
    """``durees_passage.csv`` : médiane, quartiles et moyenne d'un passage, avec repli par spécialité."""

    def __init__(self, table: pd.DataFrame):
        super().__init__(table, ["specialite", "mode_entree", "classe_age"])

    def chercher(self, specialite: str, mode_entree: str = "", classe_age: str = "") -> dict[str, float] | None:
        cle = {"specialite": specialite}
        if mode_entree:
            cle["mode_entree"] = mode_entree
        if classe_age:
            cle["classe_age"] = classe_age
        r = self.chercher_bloc(**cle)
        if r is None:
            return None
        g, niveau = r
        ligne = g.iloc[0]
        return {"mediane_j": float(ligne["mediane_j"]), "q1_j": float(ligne["q1_j"]), "q3_j": float(ligne["q3_j"]),
                "moyenne_j": float(ligne["moyenne_j"]), "niveau": niveau}


class SoinsCritiques:
    """``soins_critiques_ghm.csv`` : probabilités de passer en réanimation, soins intensifs, surveillance continue."""

    def __init__(self, table: pd.DataFrame):
        self._fin = {(r["ghm"], r["classe_age_duree"]): (r["part_rea"], r["part_usi"], r["part_usc"]) for _, r in table.iterrows()}
        par_ghm = table.groupby("ghm")[["part_rea", "part_usi", "part_usc"]].mean()
        self._ghm = {k: tuple(v) for k, v in par_ghm.iterrows()}
        par_racine = table.assign(racine=table["ghm"].str[:5]).groupby("racine")[["part_rea", "part_usi", "part_usc"]].mean()
        self._racine = {k: tuple(v) for k, v in par_racine.iterrows()}

    def probabilites(self, ghm: str, classe_age_duree: str) -> dict[str, float]:
        p = self._fin.get((ghm, classe_age_duree)) or self._ghm.get(ghm) or self._racine.get(ghm[:5]) or (0.0, 0.0, 0.0)
        return {"rea": float(p[0]), "usi": float(p[1]), "usc": float(p[2])}


class ClassesTension:
    """``classes_tension.csv`` : les bornes des classes, et la classe d'une valeur de tension."""

    def __init__(self, table: pd.DataFrame, facteur: float = 1.0):
        self.brute = table.copy()
        t = table.copy()
        t["borne_inf"] = t["borne_inf"].astype(float) * facteur
        t["borne_sup"] = t["borne_sup"].astype(float) * facteur
        self.table = t.sort_values("borne_inf", na_position="first").reset_index(drop=True)
        self.classes = list(self.table["classe"])
        self._sup = self.table["borne_sup"].to_numpy()

    def classe(self, tension: float) -> str:
        for cl, sup in zip(self.classes, self._sup):
            if np.isnan(sup) or tension < sup:
                return cl
        return self.classes[-1]

    def ajustees(self, facteur: float) -> "ClassesTension":
        """Les mêmes bornes à l'échelle d'un hôpital dont les lits sont multipliés par ``facteur``."""
        return ClassesTension(self.brute, facteur)


# ---------------------------------------------------------------------------
# L'ensemble des paramètres
# ---------------------------------------------------------------------------

FICHIERS_NOMBRES = {
    "durees_ghm.csv": ("part", "moyenne_60plus"),
    "soins_critiques_ghm.csv": ("part_rea", "part_usi", "part_usc"),
    "premier_service_racine.csv": ("part", "rang"),
    "table_passage.csv": ("part", "rang"),
    "durees_passage.csv": ("mediane_j", "q1_j", "q3_j", "moyenne_j"),
    "classes_tension.csv": ("borne_inf", "borne_sup"),
    "cibles.csv": ("observe", "min", "max"),
}


@dataclass
class Parametres:
    dossier: Path
    structure: Structure
    specialites: pd.DataFrame
    regroupements: pd.DataFrame
    niveaux: TableTirage                  # racine × classe_age → niveau
    durees_ghm: TableTirage               # ghm × classe_age_duree → duree_j ; moyenne_60plus en annexe
    soins_critiques: SoinsCritiques
    sexe_racine: TableTirage              # racine → sexe
    sorties: TableTirage                  # racine × niveau × classe_age → mode_sortie
    premier_service: TableTirage          # racine × classe_age → specialite (rang en annexe), restreinte à la structure
    casemix: TableTirage                  # specialite → (classe_age, racine_ghm)
    table_passage: TableTirage            # niveau × service × precedent × classe_age × mode_entree → suite (rang)
    durees_passage: DureesPassage
    venues: TableTirage                   # saison × periode × classe_age → (situation, gravite, racine_ghm)
    arrivees: dict[str, tuple[ProfilSemaine, ProfilMois]]   # par saison
    patients: TableTirage                 # → (classe_age, sexe)
    gravite: TableTirage                  # classe_age × situation × tranche_h → gravite
    classes_tension: ClassesTension
    durees_urgences: TableTirage          # situation × gravite × classe_age × classe_tension → tranche_h
    supplement: TableTirage               # classe_tension → tranche_h
    entrees_programmees: ProfilSemaine
    profil_sorties: ProfilSemaine
    cibles: pd.DataFrame
    racines_transferees: set[str]         # aucune spécialité d'accueil conventionnelle dans l'hôpital
    saisons: list[str]

    # -- chargement ---------------------------------------------------------

    @classmethod
    def charger(cls, dossier: str | Path, verifier: bool = True) -> "Parametres":
        dossier = Path(dossier)
        if verifier and not verifier_dossier(dossier, []):
            raise ValueError(f"le dossier {dossier} n'est pas un jeu de paramètres complet et cohérent (voir ci-dessus)")

        def lire(nom: str) -> pd.DataFrame:
            return lire_csv(dossier / nom, FICHIERS_NOMBRES.get(nom, ("part",)))

        specialites = lire("specialites.csv")
        structure = Structure.depuis_yaml(dossier / "structure.yaml", dict(zip(specialites["specialite"], specialites["type"])))
        services = list(structure.services)
        conventionnels = {s.code for s in structure.conventionnels}

        # premier service : restreint à la structure ; racines sans service conventionnel → transfert
        ps = restreindre_services(lire("premier_service_racine.csv"), services, ["racine_ghm", "classe_age"])
        avec_accueil = set(ps.loc[ps["specialite"].isin(conventionnels), "racine_ghm"])
        racines_transferees = set(lire("premier_service_racine.csv")["racine_ghm"]) - avec_accueil
        # un bloc qui n'a plus que des soins critiques n'est pas un bloc d'accueil : on le retire,
        # la recherche tombera sur le repli (ou sur le transfert si la racine entière est perdue)
        bloc_conv = ps.groupby(["racine_ghm", "classe_age"])["specialite"].transform(lambda s: s.isin(conventionnels).any())
        ps = ps[bloc_conv & ~ps["racine_ghm"].isin(racines_transferees)]

        # table de passage : service et précédent dans la structure, suite dans la structure ou « sortie »
        tp = lire("table_passage.csv")
        tp = tp[tp["service"].isin(services) & tp["precedent"].isin(services + ["AUCUN", ""])]
        tp = restreindre_services(tp, services + ["sortie"], ["niveau", "service", "precedent", "classe_age", "mode_entree"], colonne_service="suite")
        tp["rang"] = attribuer_rang(tp, masque=tp["suite"].ne("sortie"))

        arrivees_t = lire("profil_arrivees_urgences.csv")
        arrivees = {s: (ProfilSemaine(g), ProfilMois(g)) for s, g in arrivees_t.groupby("saison")}
        venues_t = lire("venues_urgences.csv")

        return cls(
            dossier=dossier, structure=structure, specialites=specialites, regroupements=lire("regroupements_ghm.csv"),
            niveaux=TableTirage(lire("niveaux.csv"), ["racine_ghm", "classe_age"], ["niveau"]),
            durees_ghm=TableTirage(lire("durees_ghm.csv"), ["ghm", "classe_age_duree"], ["duree_j"], annexes=["moyenne_60plus"]),
            soins_critiques=SoinsCritiques(lire("soins_critiques_ghm.csv")),
            sexe_racine=TableTirage(lire("sexe_racine.csv"), ["racine_ghm"], ["sexe"]),
            sorties=TableTirage(lire("sorties.csv"), ["racine_ghm", "niveau", "classe_age"], ["mode_sortie"]),
            premier_service=TableTirage(ps, ["racine_ghm", "classe_age"], ["specialite"], annexes=["rang"]),
            casemix=TableTirage(lire("casemix.csv"), ["specialite"], ["classe_age", "racine_ghm"]),
            table_passage=TableTirage(tp, ["niveau", "service", "precedent", "classe_age", "mode_entree"], ["suite"], annexes=["rang"]),
            durees_passage=DureesPassage(lire("durees_passage.csv")),
            venues=TableTirage(venues_t, ["saison", "periode", "classe_age"], ["situation", "gravite", "racine_ghm"]),
            arrivees=arrivees,
            patients=TableTirage(lire("profil_patients_urgences.csv"), [], ["classe_age", "sexe"]),
            gravite=TableTirage(lire("gravite_urgences.csv"), ["classe_age", "situation", "tranche_h"], ["gravite"]),
            classes_tension=ClassesTension(lire("classes_tension.csv")),
            durees_urgences=TableTirage(lire("durees_passage_urgences.csv"), ["situation", "gravite", "classe_age", "classe_tension"], ["tranche_h"]),
            supplement=TableTirage(lire("supplement_rang2.csv"), ["classe_tension"], ["tranche_h"]),
            entrees_programmees=ProfilSemaine(lire("profil_entrees_programmees.csv")),
            profil_sorties=ProfilSemaine(lire("profil_sorties.csv")),
            cibles=lire("cibles.csv"),
            racines_transferees=racines_transferees,
            saisons=sorted(set(venues_t["saison"]) & set(arrivees)),
        )

    # -- commodités -----------------------------------------------------------

    @property
    def saison(self) -> str:
        """La saison de référence (structure.yaml, sinon la plus récente disponible)."""
        s = self.structure.urgences.get("saison")
        return s if s in self.saisons else self.saisons[-1]

    def libelle_racine(self, racine: str) -> str:
        r = self.regroupements[self.regroupements["racine_ghm"] == racine]
        return "" if r.empty else str(r["libelle"].iloc[0]).rsplit(", niveau", 1)[0]

    def resume(self) -> str:
        """Ce que le module a compris des fichiers, en quelques lignes."""
        st = self.structure
        lignes = [
            f"{st.etablissement} — {st.description}",
            f"{len(st.services)} services, {sum(s.lits for s in st.services.values())} lits : "
            f"{len(st.conventionnels)} conventionnels, {len(st.soins_critiques)} de soins critiques, urgences = {st.service_urgences.code}",
            f"présents visés : {sum(s.presents_vises or 0 for s in st.conventionnels):.0f} dans les services conventionnels",
            f"saisons disponibles : {', '.join(self.saisons)} ; saison de référence : {self.saison} ; "
            f"{st.urgences.get('passages_par_an')} passages par an",
            f"racines : {len(self.niveaux.blocs(('racine_ghm',)))} dans niveaux, {len(self.premier_service.blocs(('racine_ghm',)))} "
            f"avec un service d'accueil, {len(self.racines_transferees)} transférées"
            + (f" ({', '.join(sorted(self.racines_transferees)[:8])})" if self.racines_transferees else ""),
            f"premier service : {len(self.premier_service)} lignes après restriction, spécialités "
            f"{sorted(set(c[0] for l in self.premier_service._lignes[self.premier_service.niveaux[0]].values() for c in l.categories))}",
            f"table de passage : {len(self.table_passage)} lignes après restriction, niveaux {self.table_passage.niveaux}",
            f"venues : {len(self.venues)} lignes, niveaux {self.venues.niveaux}",
            f"durées de passage aux urgences : niveaux {self.durees_urgences.niveaux}",
            f"classes de tension : {', '.join(f'{c} (<{s:.0f})' if not np.isnan(s) else c for c, s in zip(self.classes_tension.classes, self.classes_tension._sup))}",
            f"cibles : {len(self.cibles)} lignes",
        ]
        return "\n".join(lignes)
