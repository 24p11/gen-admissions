"""Le moteur : les processus SimPy qui font vivre l'hôpital.

Il assemble les trois modules précédents — ``Parametres`` (ce qu'on sait), ``Tireur``
(fabriquer un patient), ``ParcDeLits`` (obtenir un lit) — et ajoute ce qui n'existe
qu'en mouvement : le calendrier, la tension du jour, les flux d'arrivée, le
déroulement d'un passage aux urgences et d'un séjour, les retours. Tout ce qui se
passe est noté dans un ``Journal`` (des listes de dictionnaires), matière première
d'``export.py``.

Le temps SimPy est en **heures**, à partir de ``origine`` = début de la simulation
moins la chauffe. ``Simulation.date(t)`` rend la date-heure correspondante.

Les processus :

- **relevé de tension**, chaque jour à ``heure_releve_tension`` : on compte les
  patients présents venus des urgences (UHCD comprise), on en déduit la classe de
  tension (bornes ``classes_tension``, ajustées au prorata des lits si un scénario
  les change), qui vaut jusqu'au relevé suivant ;
- **flux des urgences** : chaque heure, un nombre d'arrivées tiré en Poisson autour
  de ``passages_par_an`` × profil de la semaine × facteur du mois ; chaque arrivée
  devient un ``passage_urgences`` ;
- **passage aux urgences** : la venue est tirée (A2-A7) ; consultation, transfert
  et décès sortent à la fin de leur durée ; hospitalisation et mono-UHCD demandent
  un lit d'UHCD à la ``seuil_uhcd_h``ᵉ heure (lit brancard s'il n'y en a pas) ; à la
  fin de la durée tirée, l'hospitalisé demande son lit de service (règle D) ;
- **flux programmé**, par service : les entrées de la journée (Poisson autour d'un
  rythme réglé par la loi de Little sur ``presents_vises``, corrigé chaque jour par
  l'écart entre présents observés et visés), aux heures du profil ; un patient sans
  lit au-delà du délai de report revient le lendemain ;
- **séjour** : la trajectoire (soins critiques en tête s'ils ont été tirés, puis le
  service obtenu, puis ``suite_parcours`` jusqu'à la sortie), la durée totale
  répartie entre les étapes au prorata des durées médianes de passage, chaque
  mutation passant par la règle D (le patient garde son lit en attendant), l'heure
  de sortie tirée sur le profil des sorties sauf dans les services exemptés ;
- **retours** : un patient qui sort rentre dans la mémoire des patients connus ; une
  nouvelle venue lui est attribuée avec la probabilité de retour de sa classe d'âge
  (même ``ipp``).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import simpy

from .lits import ParcDeLits, ResultatLit
from .parametres import Parametres
from .tirages import Patient, Tireur

FRACTIONS_SOINS_CRITIQUES = {"rea": 0.35, "usi": 0.30, "usc": 0.25}
MAX_ETAPES = 6
MAX_REPORTS = 3
DELAI_MUTATION_H = 72.0            # au-delà, la mutation est abandonnée : le patient finit son séjour où il est
CODES_SORTIE = {"domicile": ("8", ""), "EHPAD": ("8", "7"), "SSR": ("7", "2"), "transfert": ("7", "1"), "deces": ("9", "")}
GAIN_REGULATION = 0.15             # réglage quotidien du rythme programmé : part de l'écart corrigée chaque jour
FENETRE_REGULATION_J = 7


@dataclass
class Journal:
    """Tout ce qui s'est passé, en listes plates (une entrée par événement)."""
    venues: list[dict] = field(default_factory=list)
    sejours: list[dict] = field(default_factory=list)
    rums: list[dict] = field(default_factory=list)
    mouvements: list[dict] = field(default_factory=list)
    tension: list[dict] = field(default_factory=list)
    reports: list[dict] = field(default_factory=list)
    programme: list[dict] = field(default_factory=list)      # le rythme programmé jour par jour (contrôle)

    def tableaux(self) -> dict[str, pd.DataFrame]:
        return {k: pd.DataFrame(getattr(self, k)) for k in ("venues", "sejours", "rums", "mouvements", "tension", "reports", "programme")}


@dataclass
class Compteurs:
    ipp: int = 0
    nas: int = 0
    no_visite: int = 0
    transferts_faute_de_service: int = 0
    transferts_faute_de_lit: int = 0
    brancards: int = 0
    mutations_abandonnees: int = 0


class Simulation:
    """Une simulation prête à être lancée : ``Simulation(p).lancer()`` puis ``journal``."""

    def __init__(self, p: Parametres, graine: int | None = None, duree_jours: int | None = None, chauffe_jours: int | None = None,
                 marge_fin_jours: int | None = None, capacites: dict[str, int] | None = None, facteur_lits: float = 1.0):
        self.p = p
        reglages = p.structure.simulation
        self.graine = reglages.get("graine", 42) if graine is None else graine
        self.rng = np.random.default_rng(self.graine)
        self.duree_jours = int(reglages.get("duree_jours", 365) if duree_jours is None else duree_jours)
        self.chauffe_jours = int(reglages.get("chauffe_jours", 42) if chauffe_jours is None else chauffe_jours)
        self.marge_fin_jours = int(reglages.get("marge_fin_jours", 60) if marge_fin_jours is None else marge_fin_jours)
        self.debut = pd.Timestamp(p.structure.debut)                        # début de la période utile
        self.origine = self.debut - pd.Timedelta(days=self.chauffe_jours)   # t = 0
        self.horizon_h = 24 * (self.chauffe_jours + self.duree_jours + self.marge_fin_jours)
        self.env = simpy.Environment()
        self.tireur = Tireur(p, self.rng)
        self.parc = ParcDeLits(self.env, p, self.rng, capacites)
        self.classes_tension = p.classes_tension.ajustees(facteur_lits) if facteur_lits != 1.0 else p.classes_tension
        self.journal = Journal()
        self.compteurs = Compteurs()
        self.classe_tension = "moyenne"
        self.presents_urgences_releve = 0
        self.saison = p.saison
        self.service_urgences = p.structure.service_urgences.code
        self.seuil_uhcd_h = float(p.structure.urgences.get("seuil_uhcd_h", 6))
        self.heure_releve = float(p.structure.lits.get("heure_releve_tension", 8))
        self.sans_effet_weekend = set(p.structure.sorties.get("services_sans_effet_weekend", []))
        self.retours = p.structure.population.get("probabilite_retour", {})
        self.connus: dict[str, deque] = {c: deque(maxlen=5000) for c in p.structure.classes_age}
        # rythme programmé : entrées par jour, par service, réglé en cours de route
        self.rythme: dict[str, float] = {}
        self.rythme_max: dict[str, float] = {}
        self.historique_presents: dict[str, deque] = {}
        self.reports_recents: dict[str, deque] = {}        # (entrées, reports) des derniers jours, par service
        for s in p.structure.conventionnels:
            if s.presents_vises and p.casemix.chercher(specialite=s.code) is not None:
                d = p.durees_passage.chercher(s.code, "programme") or p.durees_passage.chercher(s.code)
                moyenne_j = max(0.5, d["moyenne_j"]) if d else 5.0
                part_urg = self._part_urgences_cible(s.code)
                self.rythme[s.code] = max(0.05, s.presents_vises * (1 - part_urg) / moyenne_j)
                self.rythme_max[s.code] = 1.5 * s.presents_vises / moyenne_j      # jamais plus que de quoi remplir le service seul
                self.historique_presents[s.code] = deque(maxlen=FENETRE_REGULATION_J)
                self.reports_recents[s.code] = deque(maxlen=FENETRE_REGULATION_J)

    # -- temps -----------------------------------------------------------------

    def date(self, t: float) -> pd.Timestamp:
        return self.origine + pd.Timedelta(hours=float(t))

    def jour_semaine(self, t: float) -> int:
        return self.date(t).dayofweek + 1

    def mois(self, t: float) -> int:
        return self.date(t).month

    def _part_urgences_cible(self, service: str) -> float:
        c = self.p.cibles
        r = c[(c["indicateur"] == "part_urgences") & (c["service"] == service)]
        return float(r["observe"].iloc[0]) if len(r) and pd.notna(r["observe"].iloc[0]) else 0.4

    # -- lancement ------------------------------------------------------------

    def lancer(self, jusqu_a_h: float | None = None) -> "Simulation":
        env = self.env
        env.process(self.releve_tension())
        env.process(self.flux_urgences())
        for service in self.rythme:
            env.process(self.flux_programme(service))
        env.run(until=self.horizon_h if jusqu_a_h is None else jusqu_a_h)
        return self

    # -- tension ---------------------------------------------------------------

    def presents_urgences(self) -> int:
        return self.parc.presents(filtre=lambda pt: getattr(pt, "origine", "") == "urgences")

    def releve_tension(self):
        yield self.env.timeout(self.heure_releve)
        while True:
            n = self.presents_urgences()
            self.presents_urgences_releve = n
            self.classe_tension = self.classes_tension.classe(n)
            self.journal.tension.append({"date": self.date(self.env.now).normalize(), "tension": n, "classe_tension": self.classe_tension,
                                         **{f"occupes_{c}": l.occupes for c, l in self.parc.lits.items()}})
            yield self.env.timeout(24.0)

    # -- urgences ---------------------------------------------------------------

    def flux_urgences(self):
        passages_an = float(self.p.structure.urgences.get("passages_par_an", 40_000))
        semaine, mois = self.p.arrivees[self.saison]
        par_semaine = passages_an * 7 / 365.25
        while True:
            t = self.env.now
            attendu = par_semaine * semaine.poids[self.jour_semaine(t) - 1, int(t % 24)] * mois.facteur(self.mois(t))
            n = int(self.rng.poisson(attendu))
            for delai in np.sort(self.rng.random(n)):
                self.env.process(self.passage_urgences(t + float(delai)))
            yield self.env.timeout(1.0)

    def _nouveau_patient_ou_connu(self, patient: Patient) -> int:
        """L'ipp : celui d'un patient connu de même classe d'âge et sexe avec la probabilité de retour, sinon un nouveau."""
        p_retour = float(self.retours.get(patient.classe_age, 0.0))
        candidats = self.connus.get(patient.classe_age)
        if candidats and self.rng.random() < p_retour:
            for _ in range(5):
                ipp, sexe, age = candidats[int(self.rng.integers(len(candidats)))]
                if sexe == patient.sexe:
                    patient.age = age
                    return ipp
        self.compteurs.ipp += 1
        return self.compteurs.ipp

    def passage_urgences(self, t_arrivee: float):
        env, p, c = self.env, self.p, self.compteurs
        if t_arrivee > env.now:
            yield env.timeout(t_arrivee - env.now)
        patient = self.tireur.venue_urgences(self.saison, self.mois(t_arrivee), self.classe_tension)
        patient.ipp = self._nouveau_patient_ou_connu(patient)
        c.no_visite += 1
        patient.no_visite = c.no_visite
        venue = {"no_visite": patient.no_visite, "ipp": patient.ipp, "entree": self.date(t_arrivee), "age": patient.age, "sexe": patient.sexe,
                 "gravite": patient.gravite, "situation": patient.situation, "classe_tension": patient.classe_tension,
                 "service_prevu": patient.service_prevu, "rang": None, "attente_lit_h": 0.0, "uhcd": False, "brancard": False,
                 "nas": None, "mode_sortie": "", "sortie": None, "regle": ""}
        self.journal.venues.append(venue)
        duree = patient.duree_urgences_h

        if patient.situation in ("consultation", "transfert", "deces"):
            yield env.timeout(duree)
            venue["sortie"] = self.date(env.now)
            venue["mode_sortie"] = {"consultation": "8", "transfert": "7", "deces": "9"}[patient.situation]
            return

        # hospitalisation ou mono-UHCD : un lit d'UHCD à la 6e heure si le passage dure plus
        uhcd = self.parc.lits[self.service_urgences]
        occ_uhcd = None
        if duree > self.seuil_uhcd_h:
            yield env.timeout(self.seuil_uhcd_h)
            req = uhcd.demander(self.parc.priorites["urgences"])
            reste = env.timeout(duree - self.seuil_uhcd_h)
            yield simpy.AnyOf(env, [req, reste])
            if req.triggered:
                occ_uhcd = uhcd.installer(req, patient)
                venue["uhcd"] = True
                t_uhcd = env.now
                if not reste.triggered:
                    yield reste
            else:
                uhcd.retirer(req)
                venue["brancard"] = True
                c.brancards += 1
        else:
            yield env.timeout(duree)
        t_fin_passage = env.now

        if patient.situation == "mono_uhcd":
            c.nas += 1
            patient.nas = c.nas
            venue["nas"] = patient.nas
            venue["mode_sortie"] = "6"
            venue["sortie"] = self.date(t_fin_passage)
            debut_sejour = t_uhcd if occ_uhcd else t_arrivee + min(duree, self.seuil_uhcd_h)
            self._ecrire_mono_uhcd(patient, debut_sejour, t_fin_passage, occ_uhcd is not None)
            if occ_uhcd:
                uhcd.liberer(occ_uhcd)
            self._memoriser(patient)
            return

        # hospitalisation : le lit de service (règle D)
        if patient.transfert:
            c.transferts_faute_de_service += 1
            venue.update(mode_sortie="7", sortie=self.date(env.now), regle="aucun_service")
            if occ_uhcd:
                uhcd.liberer(occ_uhcd)
            return
        resultat: ResultatLit = yield env.process(self.parc.obtenir_lit(
            patient, patient.services_candidats, patient.service_prevu, "urgences", self.classe_tension,
            supplement=self.tireur.supplement_rang2_h))
        venue["attente_lit_h"] = resultat.attente_h
        venue["regle"] = resultat.regle
        if resultat.issue != "lit":
            c.transferts_faute_de_lit += 1
            venue.update(mode_sortie="7", sortie=self.date(env.now))
            if occ_uhcd:
                uhcd.liberer(occ_uhcd)
            return
        c.nas += 1
        patient.nas = c.nas
        venue.update(nas=patient.nas, mode_sortie="6", sortie=self.date(env.now), rang=resultat.rang)
        t_entree_sejour = env.now
        mouvements_uhcd = []
        if occ_uhcd:
            mouvements_uhcd.append((self.service_urgences, t_uhcd, env.now, occ_uhcd.lit, "entree"))
            uhcd.liberer(occ_uhcd)
        yield env.process(self.sejour(patient, resultat, t_entree_sejour, "urgences", mouvements_uhcd))

    # -- programmé -----------------------------------------------------------------

    def flux_programme(self, service: str):
        env = self.env
        profil = self.p.entrees_programmees
        while True:
            t = env.now
            jour = self.jour_semaine(t)
            self._regler_rythme(service)
            attendu = self.rythme[service] * profil.part_jour(jour) * 7
            n = int(self.rng.poisson(attendu))
            heures = sorted(profil.tirer_heure(self.rng, jour) for _ in range(n))
            self._reports_du_jour = getattr(self, "_reports_du_jour", {})
            reports_hier = self._reports_du_jour.pop(service, 0)
            self.reports_recents[service].append((n, reports_hier))
            self.journal.programme.append({"date": self.date(t).normalize(), "service": service, "rythme": self.rythme[service], "entrees": n,
                                           "reports_veille": reports_hier, "occupes": self.parc.lits[service].occupes})
            for h in heures:
                env.process(self.entree_programmee(service, t + h))
            yield env.timeout(24.0)

    def _regler_rythme(self, service: str) -> None:
        """Loi de Little en boucle fermée : si les présents observés sont sous les présents visés, on programme
        un peu plus ; si les entrées programmées sont reportées faute de lit, un peu moins (le service est plein,
        programmer davantage ne ferait qu'allonger la file)."""
        s = self.p.structure.services[service]
        hist = self.historique_presents[service]
        hist.append(self.parc.lits[service].occupes)
        if len(hist) >= 3 and s.presents_vises:
            ecart = (s.presents_vises - float(np.mean(hist))) / s.presents_vises
            recents = self.reports_recents[service]
            entrees = sum(e for e, _ in recents); reports = sum(r for _, r in recents)
            part_reportee = reports / (entrees + reports) if entrees + reports else 0.0
            self.rythme[service] = float(np.clip(self.rythme[service] * (1 + GAIN_REGULATION * (ecart - part_reportee)),
                                                 0.02, self.rythme_max[service]))

    def entree_programmee(self, service: str, t_prevu: float, patient: Patient | None = None, reports: int = 0):
        env, c = self.env, self.compteurs
        if t_prevu > env.now:
            yield env.timeout(t_prevu - env.now)
        if patient is None:
            patient = self.tireur.entree_programmee(service)
            patient.ipp = self._nouveau_patient_ou_connu(patient)
        resultat: ResultatLit = yield env.process(self.parc.obtenir_lit(patient, patient.services_candidats, service, "programme", self.classe_tension))
        if resultat.issue != "lit":
            self.journal.reports.append({"date": self.date(t_prevu), "service": service, "report": reports + 1})
            self._reports_du_jour = getattr(self, "_reports_du_jour", {})
            self._reports_du_jour[service] = self._reports_du_jour.get(service, 0) + 1
            if reports + 1 < MAX_REPORTS:                       # B4 : reporté au lendemain à la même heure
                env.process(self.entree_programmee(service, t_prevu + 24.0, patient, reports + 1))
            return
        c.nas += 1
        patient.nas = c.nas
        yield env.process(self.sejour(patient, resultat, env.now, "programme", [], reports=reports))

    # -- séjour -------------------------------------------------------------------

    def _trajectoire(self, patient: Patient, premier: str) -> list[tuple[str, int | None, str]]:
        """Les étapes du séjour : (service, rang, règle d'obtention), soins critiques en tête, puis
        le premier service, puis la suite tirée dans la table de passage jusqu'à la sortie."""
        st = self.p.structure
        etapes: list[tuple[str, int | None, str]] = []
        for type_, present in patient.soins_critiques.items():
            if present:
                services = [s.code for s in st.services_pour(type_) if s.code != premier]
                if services:
                    etapes.append((services[0], 1, "soins_critiques"))
        if premier not in [e[0] for e in etapes]:
            etapes.append((premier, None, "premier"))
        precedent, service, mode = "AUCUN", premier, patient.origine
        while len(etapes) < MAX_ETAPES:
            suite, rang, _ = self.tireur.suite_parcours(service, precedent, patient.classe_age, mode)
            if suite == "sortie" or suite not in st.services or suite == service:
                break
            etapes.append((suite, rang, "mutation"))
            precedent, service = service, suite
        return etapes

    def _repartir_duree(self, patient: Patient, etapes: list[tuple[str, int | None, str]]) -> list[float]:
        """La durée totale (heures) répartie entre les étapes : soins critiques à leur fraction, le reste au prorata
        des durées médianes de passage de chaque spécialité."""
        total_h = max(2.0, patient.duree_sejour_j * 24)
        poids = []
        fixes = 0.0
        for service, _, regle in etapes:
            if regle == "soins_critiques":
                frac = max(FRACTIONS_SOINS_CRITIQUES.get(t, 0.25) for t, v in patient.soins_critiques.items() if v) if any(patient.soins_critiques.values()) else 0.25
                poids.append(-frac)          # négatif : une fraction du total, pas un poids
                fixes += frac
            else:
                d = self.p.durees_passage.chercher(service, patient.origine, patient.classe_age) or self.p.durees_passage.chercher(service)
                poids.append(d["mediane_j"] if d else 1.0)
        fixes = min(fixes, 0.7)
        reste = total_h * (1 - fixes)
        somme = sum(w for w in poids if w > 0) or 1.0
        return [total_h * -w if w < 0 else reste * w / somme for w in poids]

    def _heure_de_sortie(self, t_sortie: float, service: str) -> float:
        """L'heure de sortie tirée sur le profil des sorties (le jour de la sortie prévue), sauf services exemptés."""
        if service in self.sans_effet_weekend:
            return t_sortie
        jour_debut = (t_sortie // 24) * 24
        h = self.p.profil_sorties.tirer_heure(self.rng, self.jour_semaine(t_sortie))
        t = jour_debut + h
        return t if t > t_sortie - 12 else t + 24

    def sejour(self, patient: Patient, premier: ResultatLit, t_entree: float, origine: str, mouvements_avant: list, reports: int = 0):
        env, c, p = self.env, self.compteurs, self.p
        etapes = self._trajectoire(patient, premier.service)
        durees = self._repartir_duree(patient, etapes)
        # le premier lit obtenu est celui du service "premier" ; si des soins critiques précèdent, il faut d'abord ce lit-là
        rums, mouvements = [], list(mouvements_avant)
        service_courant, occ = premier.service, premier.occupation
        t_debut_etape = t_entree
        # si la trajectoire commence par les soins critiques, le lit de service déjà obtenu est rendu, et redemandé plus tard
        indice_premier = next(i for i, e in enumerate(etapes) if e[0] == premier.service) if any(e[0] == premier.service for e in etapes) else 0
        if indice_premier > 0:
            self.parc.lits[premier.service].liberer(occ)
            resultat = yield env.process(self.parc.obtenir_lit(patient, [(etapes[0][0], 1.0, 1)], etapes[0][0], origine, self.classe_tension))
            if resultat.issue != "lit":
                etapes = etapes[indice_premier:]           # pas de lit de soins critiques : le séjour se fait sans
                durees = self._repartir_duree(patient, etapes)
                resultat = yield env.process(self.parc.obtenir_lit(patient, patient.services_candidats or [(premier.service, 1.0, 1)], premier.service, origine, self.classe_tension))
                if resultat.issue != "lit":
                    return
            service_courant, occ = resultat.service, resultat.occupation
            t_debut_etape = env.now
        norum = len(mouvements_avant)
        mdeeue, cdpu = "8", ("5" if origine == "urgences" else "")
        for i, ((service, rang, regle), duree_h) in enumerate(zip(etapes, durees)):
            derniere = i == len(etapes) - 1
            fin_prevue = t_debut_etape + duree_h
            if derniere:
                fin_prevue = self._heure_de_sortie(fin_prevue, service_courant)
            yield env.timeout(max(0.0, fin_prevue - env.now))
            suivant = etapes[i + 1] if not derniere else None
            occ_suivante = None
            if suivant is not None:
                prio = "sortie_soins_critiques" if p.structure.services[service_courant].soins_critiques else "mutation"
                resultat = yield env.process(self.parc.obtenir_lit(patient, [(suivant[0], 1.0, suivant[1] or 1)], suivant[0], prio,
                                                                   self.classe_tension, delai_max_h=DELAI_MUTATION_H))
                if resultat.issue == "lit":
                    occ_suivante = resultat
                else:
                    c.mutations_abandonnees += 1
            t_fin = env.now
            norum += 1
            rums.append({"norum": norum, "spe_uma": service_courant, "d8eeue": self.date(t_debut_etape), "d8soue": self.date(t_fin),
                         "mdeeue": mdeeue, "cdpu": cdpu, "mdsoue": "6" if occ_suivante is not None else None,
                         "sim_service_prevu": patient.service_prevu if i == indice_premier else (suivant and suivant[0]) or "",
                         "sim_rang": premier.rang if i == indice_premier else rang, "sim_regle": premier.regle if i == indice_premier else regle,
                         "sim_attente_h": premier.attente_h if i == indice_premier else 0.0,
                         "sim_supplement_h": premier.supplement_h if i == indice_premier else 0.0})
            mouvements.append((service_courant, t_debut_etape, t_fin, occ.lit, "entree" if not mouvements else "mutation"))
            self.parc.lits[service_courant].liberer(occ)
            mdeeue, cdpu = "6", ""
            if occ_suivante is None:
                if not derniere:
                    # mutation impossible : on reste dans le service courant pour le temps qui restait
                    reste = sum(durees[i + 1:])
                    req = self.parc.lits[service_courant].demander(self.parc.priorites["mutation"])
                    yield req
                    occ = self.parc.lits[service_courant].installer(req, patient)
                    t_debut_etape = env.now
                    yield env.timeout(reste)
                    t_fin = env.now
                    norum += 1
                    rums.append({**rums[-1], "norum": norum, "d8eeue": self.date(t_debut_etape), "d8soue": self.date(t_fin), "mdeeue": "6",
                                 "sim_service_prevu": suivant[0], "sim_rang": None, "sim_regle": "mutation_abandonnee", "sim_attente_h": 0.0, "sim_supplement_h": 0.0})
                    mouvements.append((service_courant, t_debut_etape, t_fin, occ.lit, "changement_lit"))
                    self.parc.lits[service_courant].liberer(occ)
                break
            service_courant, occ = occ_suivante.service, occ_suivante.occupation
            rums[-1]["mdsoue"] = "6"
            t_debut_etape = env.now
        self._ecrire_sejour(patient, origine, rums, mouvements, reports)
        self._memoriser(patient)

    # -- écriture dans le journal ---------------------------------------------------

    def _ecrire_sejour(self, patient: Patient, origine: str, rums: list[dict], mouvements: list, reports: int) -> None:
        schpmsi, dest = CODES_SORTIE.get(patient.mode_sortie, ("8", ""))
        for r in rums:
            if r["mdsoue"] is None:
                r["mdsoue"] = schpmsi
        d8eeue = min(m[1] for m in mouvements) if mouvements else rums[0]["d8eeue"]
        d8eeue = self.date(d8eeue) if not isinstance(d8eeue, pd.Timestamp) else d8eeue
        d8soue = rums[-1]["d8soue"]
        principal = max((r for r in rums if r["spe_uma"] != self.service_urgences), key=lambda r: r["d8soue"] - r["d8eeue"], default=rums[-1])
        sejour = {"nas": patient.nas, "ipp": patient.ipp, "no_visite": getattr(patient, "no_visite", None), "agean": patient.age, "sexe": patient.sexe,
                  "ghm": patient.ghm, "gpcompx": patient.niveau, "d8eeue": d8eeue, "d8soue": d8soue,
                  "duree": int((d8soue.normalize() - d8eeue.normalize()).days), "echpmsi": "8", "prov": "5" if origine == "urgences" else "",
                  "cdpu": "5" if origine == "urgences" else "", "schpmsi": schpmsi, "dest": dest, "nb_rum": len(rums) + (1 if any(m[0] == self.service_urgences for m in mouvements) else 0),
                  "sim_origine": origine, "sim_mono_uhcd": "non", "sim_soins_critiques": ",".join(k for k, v in patient.soins_critiques.items() if v),
                  "sim_reports": reports, "sim_classe_age": patient.classe_age, "sim_racine": patient.racine_ghm, "sim_duree_tiree_j": round(patient.duree_sejour_j, 2),
                  "sim_mode_sortie": patient.mode_sortie}
        self.journal.sejours.append(sejour)
        if mouvements and mouvements[0][0] == self.service_urgences:
            s_, d_, f_, lit_, type_ = mouvements[0]
            self.journal.rums.append({"nas": patient.nas, "norum": 1, "spe_uma": self.service_urgences, "d8eeue": self.date(d_), "d8soue": self.date(f_),
                                      "mdeeue": "8", "cdpu": "5", "mdsoue": "6", "sim_service_prevu": "", "sim_rang": None, "sim_regle": "uhcd",
                                      "sim_attente_h": 0.0, "sim_supplement_h": 0.0, "noseqrum": principal["norum"], "typaut": "07A", "ipp": patient.ipp})
        for r in rums:
            self.journal.rums.append({"nas": patient.nas, "ipp": patient.ipp, "noseqrum": principal["norum"], "typaut": "", **r})
        for k, (service, d, f, lit, type_) in enumerate(mouvements, start=1):
            self.journal.mouvements.append({"nas": patient.nas, "ipp": patient.ipp, "nomvt": k, "nbmvt": len(mouvements), "spe_uma": service,
                                            "d8eeue": self.date(d), "d8soue": self.date(f), "prov": "5" if (k == 1 and origine == "urgences") else "",
                                            "lit": lit, "sim_type": type_})

    def _ecrire_mono_uhcd(self, patient: Patient, debut: float, fin: float, lit_obtenu: bool) -> None:
        d, f = self.date(debut), self.date(fin)
        self.journal.sejours.append({"nas": patient.nas, "ipp": patient.ipp, "no_visite": patient.no_visite, "agean": patient.age, "sexe": patient.sexe,
                                     "ghm": patient.ghm, "gpcompx": patient.niveau, "d8eeue": d, "d8soue": f, "duree": int((f.normalize() - d.normalize()).days),
                                     "echpmsi": "8", "prov": "5", "cdpu": "5", "schpmsi": "8", "dest": "", "nb_rum": 1, "sim_origine": "urgences",
                                     "sim_mono_uhcd": "oui", "sim_soins_critiques": "", "sim_reports": 0, "sim_classe_age": patient.classe_age,
                                     "sim_racine": patient.racine_ghm, "sim_duree_tiree_j": round(patient.duree_sejour_j, 2), "sim_mode_sortie": "domicile"})
        self.journal.rums.append({"nas": patient.nas, "ipp": patient.ipp, "norum": 1, "noseqrum": 1, "spe_uma": self.service_urgences, "typaut": "07A",
                                  "d8eeue": d, "d8soue": f, "mdeeue": "8", "cdpu": "5", "mdsoue": "8", "sim_service_prevu": "", "sim_rang": None,
                                  "sim_regle": "mono_uhcd" if lit_obtenu else "mono_uhcd_brancard", "sim_attente_h": 0.0, "sim_supplement_h": 0.0})
        self.journal.mouvements.append({"nas": patient.nas, "ipp": patient.ipp, "nomvt": 1, "nbmvt": 1, "spe_uma": self.service_urgences, "d8eeue": d, "d8soue": f,
                                        "prov": "5", "lit": 0, "sim_type": "entree"})

    def _memoriser(self, patient: Patient) -> None:
        if patient.mode_sortie != "deces":
            self.connus[patient.classe_age].append((patient.ipp, patient.sexe, patient.age))

    # -- bilan rapide ---------------------------------------------------------------

    def resume(self) -> str:
        j = self.journal
        v = pd.DataFrame(j.venues)
        s = pd.DataFrame(j.sejours)
        lignes = [f"{len(v)} venues, {len(s)} séjours, {len(j.rums)} RUM, {len(j.mouvements)} mouvements, sur {self.horizon_h / 24:.0f} jours "
                  f"(dont {self.chauffe_jours} de chauffe)"]
        if len(v):
            lignes.append("situations : " + ", ".join(f"{k} {v_:.1%}" for k, v_ in v["situation"].value_counts(normalize=True).items()))
            hosp = v[v["situation"] == "hospitalisation"]
            if len(hosp):
                lignes.append(f"hospitalisés : attente d'un lit médiane {hosp['attente_lit_h'].median():.1f} h, "
                              f"moyenne {hosp['attente_lit_h'].mean():.1f} h ; règles : {hosp['regle'].value_counts().to_dict()}")
        c = self.compteurs
        lignes.append(f"transferts faute de service {c.transferts_faute_de_service}, faute de lit {c.transferts_faute_de_lit}, "
                      f"brancards {c.brancards}, mutations abandonnées {c.mutations_abandonnees}, reports {len(j.reports)}")
        t = pd.DataFrame(j.tension)
        if len(t):
            lignes.append(f"tension : {t['tension'].mean():.0f} présents en moyenne, classes {t['classe_tension'].value_counts().to_dict()}")
            occ = t[[c_ for c_ in t.columns if c_.startswith("occupes_")]].mean()
            vises = {s_.code: s_.presents_vises for s_ in self.p.structure.conventionnels}
            lignes.append("présents moyens (visés) : " + ", ".join(f"{k[8:]} {v_:.0f} ({vises.get(k[8:], '-')})" for k, v_ in occ.items() if k[8:] in vises))
        return "\n".join(lignes)
