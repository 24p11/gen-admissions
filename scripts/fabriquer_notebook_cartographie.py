"""Fabrique notebooks/cartographie_hopital.ipynb : la cartographie d'un hôpital à
partir de ses 22 fichiers de paramètres (structure, volumes déduits, urgences,
casemix, parcours), puis lance la simulation et diagnostique ses sorties. Le notebook est paramétré par un dossier ; il tourne sur
l'hôpital fictif comme sur un dossier réel.

Usage : python scripts/fabriquer_notebook_cartographie.py
"""
from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

SORTIE = Path(__file__).resolve().parents[1] / "notebooks" / "cartographie_hopital.ipynb"

CELLULES = []
md, code = (lambda s: CELLULES.append(new_markdown_cell(s.strip("\n")))), (lambda s: CELLULES.append(new_code_cell(s.strip("\n"))))

md("""
# Cartographie d'un hôpital à partir de ses paramètres

Ce notebook lit les 22 fichiers de paramètres d'un hôpital (le format décrit dans
`parametres/DICTIONNAIRE.md`) et en tire une vue d'ensemble de son activité : sa
structure, les volumes qu'impliquent les paramètres, le fonctionnement des urgences,
le casemix, les parcours. Il sert à deux choses :

- **comprendre l'hôpital** que l'on va simuler, et vérifier que les paramètres se
  tiennent entre eux (les volumes déduits par deux chemins indépendants doivent se
  recouper) ;
- plus tard, **comparer le simulé au réel** : les mêmes graphiques, tracés sur la
  sortie du simulateur, doivent ressembler à ceux-ci.

Les fichiers ne contiennent que des proportions. Les nombres absolus (séjours par an,
hospitalisations depuis les urgences) sont donc *déduits*, par le même raisonnement
que le simulateur emploiera (section 2).
""")

code("""
from pathlib import Path
import numpy as np
import pandas as pd
import yaml
import matplotlib.pyplot as plt
import matplotlib as mpl

# le dépôt : on remonte jusqu'au dossier qui contient gen_admissions/, pour trouver le module
RACINE = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / "gen_admissions").exists())
import sys; sys.path.insert(0, str(RACINE))
from gen_admissions.extraction import restreindre_services, config_locale

plt.rcParams.update({"figure.figsize": (10, 4.5), "axes.grid": True, "grid.alpha": 0.3, "axes.spines.top": False, "axes.spines.right": False})
CLASSES_AGE = ["0-17", "18-64", "65-74", "75+"]
TRANCHES_H = ["0-1", "1-2", "2-4", "4-6", "6-12", "12-24", "24+"]
JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
""")

md("""
## Réglages

Les chemins viennent de `config_locale.yaml` à la racine du dépôt (fichier propre à la
machine, non suivi par git ; modèle : `config_locale.exemple.yaml`). Sans ce fichier, le
notebook tourne sur l'hôpital fictif du dépôt. `fichier_tension` est facultatif : la série
quotidienne de la tension n'est pas dans les 22 fichiers (seulement ses bornes) ; si un
fichier parquet avec les colonnes `date` et `tension` est indiqué et présent, le notebook
trace la série, sinon il passe.
""")

code("""
CONF = config_locale()
DOSSIER = CONF["dossier_parametres"]
FICHIER_TENSION = CONF["fichier_tension"] or (DOSSIER / "tension.parquet")
# pour produire tension.parquet, dans le notebook de préparation, après la cellule classes_tension :
#   tension_ref[tension_ref.nohop == NOHOP].to_parquet(DOSSIER_SORTIE / "tension.parquet", index=False)
print(CONF)
""")

md("""
## Lecture des fichiers

Tout est lu en texte, puis les colonnes numériques sont converties : ainsi les cases vides
des colonnes de bloc (les lignes de repli) restent des chaînes vides et non des `NaN`, ce
qui simplifie les filtres (`t[t.classe_age != ""]` sélectionne le niveau fin).
""")

code("""
def lire(nom, nombres=("part",)):
    t = pd.read_csv(DOSSIER / nom, dtype=str, keep_default_na=False)
    for c in nombres:
        if c in t.columns:
            t[c] = pd.to_numeric(t[c].replace("", np.nan))
    return t

P = {
    "structure": yaml.safe_load(open(DOSSIER / "structure.yaml", encoding="utf-8")),
    "specialites": lire("specialites.csv"),
    "regroupements": lire("regroupements_ghm.csv"),
    "niveaux": lire("niveaux.csv"),
    "durees_ghm": lire("durees_ghm.csv", ("part", "moyenne_60plus")),
    "soins_critiques": lire("soins_critiques_ghm.csv", ("part_rea", "part_usi", "part_usc")),
    "sexe_racine": lire("sexe_racine.csv"),
    "sorties": lire("sorties.csv"),
    "premier_service": lire("premier_service_racine.csv", ("part", "rang")),
    "casemix": lire("casemix.csv"),
    "table_passage": lire("table_passage.csv", ("part", "rang")),
    "durees_passage": lire("durees_passage.csv", ("mediane_j", "q1_j", "q3_j", "moyenne_j")),
    "venues": lire("venues_urgences.csv"),
    "arrivees": lire("profil_arrivees_urgences.csv"),
    "patients": lire("profil_patients_urgences.csv"),
    "gravite": lire("gravite_urgences.csv"),
    "classes_tension": lire("classes_tension.csv", ("borne_inf", "borne_sup")),
    "durees_urgences": lire("durees_passage_urgences.csv"),
    "supplement": lire("supplement_rang2.csv"),
    "entrees_programmees": lire("profil_entrees_programmees.csv"),
    "profil_sorties": lire("profil_sorties.csv"),
    "cibles": lire("cibles.csv", ("observe", "min", "max")),
}
S = P["structure"]
SAISON = S["urgences"]["saison"]                       # la saison de référence des tables saisonnières
print(S["etablissement"], "—", S["description"])
print("saison de référence :", SAISON, "; passages aux urgences par an :", S["urgences"]["passages_par_an"])
""")

md("""
## 1. La structure

Les services, leurs lits et les **présents visés** (le nombre moyen de patients que le
simulateur devra maintenir dans chaque service, mesuré sur les données). Le rapport
présents ÷ lits est une occupation *implicite* : si elle dépasse 1, le service accueille
en moyenne plus de patients qu'il n'a de lits déclarés — les lits déclarés ne sont pas les
lits réellement ouverts, c'est précisément pourquoi le modèle travaille en présents et non
en taux d'occupation. Les services de soins critiques et les urgences n'ont pas de
présents visés : leur occupation résulte entièrement des flux.
""")

code("""
services = pd.DataFrame([{"service": k, **v} for k, v in S["services"].items()])
services = services.merge(P["specialites"][["specialite", "type"]], on="specialite", how="left")
services["soins_critiques"] = services.get("soins_critiques", pd.Series([None] * len(services))).apply(lambda x: ",".join(x) if isinstance(x, list) else "")
services["occupation_implicite"] = (services["presents_vises"] / services["lits"]).round(2)
colonnes = [c for c in ["service", "libelle", "type", "lits", "presents_vises", "occupation_implicite", "soins_critiques", "age_min", "age_max", "sexe"] if c in services.columns]
print(f"{len(services)} services, {int(services['lits'].sum())} lits déclarés, "
      f"{services['presents_vises'].sum():.0f} présents visés dans les services conventionnels")
services[colonnes]
""")

code("""
conv = services[services["presents_vises"].notna()].sort_values("lits", ascending=False)
fig, ax = plt.subplots(figsize=(10, 0.4 * len(conv) + 1))
y = np.arange(len(conv))
ax.barh(y + 0.2, conv["lits"], height=0.4, label="lits déclarés", color="#c8c8c8")
ax.barh(y - 0.2, conv["presents_vises"], height=0.4, label="présents visés (moyenne observée)", color="#2a6f97")
ax.set_yticks(y); ax.set_yticklabels(conv["service"]); ax.invert_yaxis()
ax.set_xlabel("patients"); ax.set_title("Lits déclarés et présents moyens par service conventionnel"); ax.legend(loc="lower right")
plt.show()
""")

md("""
## 2. Les volumes que les paramètres impliquent

Le simulateur ne connaît pas le nombre de séjours : il le déduit de la **loi de Little**,
qui relie trois grandeurs d'un système stable : *présents = entrées par jour × durée moyenne
de passage*. Pour chaque service, les présents visés viennent de la structure et la durée
moyenne de `durees_passage.csv` (ligne de repli, toutes entrées confondues) ; on en tire les
entrées par an, puis la part venue des urgences (indicateur `part_urgences` des cibles) les
répartit entre urgences et programmé.

Un **second chemin**, indépendant, donne le nombre d'hospitalisations depuis les urgences :
passages par an × part des venues qui finissent hospitalisées, cette part étant recomposée
depuis `venues_urgences.csv` en pondérant chaque case (mois × classe d'âge) par son poids
dans les arrivées. Les deux chemins doivent donner des nombres voisins — la différence tient
aux mutations (un séjour venu des urgences entre dans plusieurs services successifs, donc
compte plusieurs entrées de service pour une seule hospitalisation) et au bruit des
estimations. Un écart très grand signalerait des paramètres incohérents.
""")

code("""
# chemin 1 : loi de Little, service par service
dp = P["durees_passage"]
moy = dp[(dp["mode_entree"] == "") & (dp["classe_age"] == "")].set_index("specialite")["moyenne_j"]
part_urg = P["cibles"].query("indicateur == 'part_urgences'").set_index("service")["observe"]
vol = conv.set_index("service")[["lits", "presents_vises"]].copy()
vol["duree_moyenne_j"] = moy.reindex(vol.index)
vol["entrees_par_an"] = (vol["presents_vises"] * 365 / vol["duree_moyenne_j"]).round()
vol["part_urgences"] = part_urg.reindex(vol.index)
vol["dont_urgences"] = (vol["entrees_par_an"] * vol["part_urgences"]).round()
vol["dont_programme"] = vol["entrees_par_an"] - vol["dont_urgences"]
total = vol[["entrees_par_an", "dont_urgences", "dont_programme"]].sum()
print(f"Entrées de service par an (loi de Little) : {total['entrees_par_an']:.0f}, "
      f"dont {total['dont_urgences']:.0f} venues des urgences et {total['dont_programme']:.0f} programmées")
vol.round(2)
""")

code("""
# chemin 2 : passages aux urgences × part d'hospitalisation, recomposée case par case
arr = P["arrivees"]; pat = P["patients"]
def entier_texte(x):
    # « 1.0 », « 1 » ou 1 → « 1 » (les fichiers plus anciens écrivaient des flottants)
    return pd.to_numeric(x).astype(int).astype(str)
poids_mois = arr[(arr["saison"] == SAISON) & (arr["dimension"] == "mois")].set_index("mois")["part"]
poids_mois.index = entier_texte(poids_mois.index)
v = P["venues"].copy(); v.loc[v["periode"] != "", "periode"] = entier_texte(v.loc[v["periode"] != "", "periode"])
poids_age = pat.groupby("classe_age")["part"].sum()
v = v[(v["saison"] == SAISON) & (v["periode"] != "")]
w = v["periode"].map(poids_mois) * v["classe_age"].map(poids_age)
situations = (v.assign(w=w).groupby(["periode", "classe_age"]).apply(lambda g: g.set_index("situation")["part"].groupby(level=0).sum() * g["w"].iloc[0]).groupby(level=-1).sum())
situations = situations / situations.sum()
passages = S["urgences"]["passages_par_an"]
print("Devenir des venues (saison", SAISON, ") :")
print((situations * 100).round(1).astype(str) + " %")
hosp_venues = passages * situations.get("hospitalisation", 0)
print(f"\\nHospitalisations depuis les urgences : {hosp_venues:.0f} par an (chemin des venues) "
      f"contre {total['dont_urgences']:.0f} entrées de service venues des urgences (loi de Little)")
print(f"Rapport entrées de service / hospitalisations = {total['dont_urgences'] / hosp_venues:.2f} "
      "(> 1 attendu : un séjour peut passer par plusieurs services)")
print(f"Séjours limités à l'UHCD : {passages * situations.get('mono_uhcd', 0):.0f} par an")
""")

code("""
fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
vv = vol.sort_values("entrees_par_an", ascending=False)
axes[0].bar(vv.index, vv["dont_urgences"], label="venues des urgences", color="#d1495b")
axes[0].bar(vv.index, vv["dont_programme"], bottom=vv["dont_urgences"], label="programmées", color="#2a6f97")
axes[0].set_title("Entrées de service par an (déduites)"); axes[0].tick_params(axis="x", rotation=90); axes[0].legend()
axes[1].pie(situations.values, labels=[f"{k} ({v:.1%})" for k, v in situations.items()], startangle=90, counterclock=False, colors=plt.cm.Set2.colors)
axes[1].set_title(f"Devenir des {passages} passages annuels aux urgences")
plt.tight_layout(); plt.show()
""")

md("""
## 3. Les urgences

### Quand arrivent les patients

La carte jour × heure (168 cases) est la dimension `semaine` du profil d'arrivées ; la courbe
mensuelle est sa dimension `mois`, une courbe par saison (septembre à août). Les deux
dimensions sont indépendantes : le simulateur combine le rythme hebdomadaire et la
saisonnalité.
""")

code("""
sem = arr[(arr["saison"] == SAISON) & (arr["dimension"] == "semaine")].copy()
sem["jour_semaine"] = pd.to_numeric(sem["jour_semaine"]).astype(int); sem["heure"] = pd.to_numeric(sem["heure"]).astype(int)
carte = sem.pivot(index="jour_semaine", columns="heure", values="part").reindex(index=range(1, 8), columns=range(24)).fillna(0)
carte = carte * passages          # en passages par an par case
fig, axes = plt.subplots(1, 2, figsize=(14, 4), gridspec_kw={"width_ratios": [3, 2]})
im = axes[0].imshow(carte.values, aspect="auto", cmap="YlOrRd")
axes[0].set_yticks(range(7)); axes[0].set_yticklabels(JOURS); axes[0].set_xticks(range(0, 24, 2)); axes[0].set_xlabel("heure d'arrivée")
axes[0].set_title(f"Arrivées par jour de semaine et heure (saison {SAISON}, passages/an par case)"); axes[0].grid(False)
plt.colorbar(im, ax=axes[0], fraction=0.03)
mois = arr[arr["dimension"] == "mois"].copy(); mois["mois"] = pd.to_numeric(mois["mois"]).astype(int)
ordre = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8]
for saison_, g in mois.groupby("saison"):
    serie = g.set_index("mois")["part"].reindex(ordre)
    axes[1].plot(range(12), serie.values * 12, marker="o", label=saison_)
axes[1].axhline(1, color="grey", lw=0.8); axes[1].set_xticks(range(12)); axes[1].set_xticklabels(ordre)
axes[1].set_title("Saisonnalité (1 = mois moyen)"); axes[1].set_xlabel("mois, de septembre à août"); axes[1].legend()
plt.tight_layout(); plt.show()
""")

md("""
### Qui arrive, et ce qui leur arrive

La pyramide âge × sexe vient de `profil_patients_urgences.csv`. Les situations par classe
d'âge sont recomposées depuis `venues_urgences.csv` (moyenne sur les mois, pondérée par les
arrivées) : c'est là qu'on lit le gradient d'âge de l'hospitalisation. La gravité CCMU des
hospitalisés (`gravite_urgences.csv`) est tirée *après* la durée de passage ; on la regarde
ici par situation.
""")

code("""
fig, axes = plt.subplots(1, 3, figsize=(15, 4))
pyr = pat.pivot(index="classe_age", columns="sexe", values="part").reindex(CLASSES_AGE).fillna(0)
axes[0].barh(CLASSES_AGE, -pyr.get("M", 0), color="#2a6f97", label="hommes"); axes[0].barh(CLASSES_AGE, pyr.get("F", 0), color="#d1495b", label="femmes")
axes[0].set_xticks(axes[0].get_xticks()); axes[0].set_xticklabels([f"{abs(x):.0%}" for x in axes[0].get_xticks()])
axes[0].set_title("Patients des urgences : âge et sexe"); axes[0].legend()

sit_age = v.assign(w=w).groupby(["classe_age", "situation"]).apply(lambda g: (g["part"] * g["w"]).sum() / g["w"].sum() if g["w"].sum() else 0).unstack(fill_value=0)
sit_age = sit_age.div(sit_age.sum(axis=1), axis=0).reindex(CLASSES_AGE)
ordre_sit = [s_ for s_ in ["consultation", "mono_uhcd", "hospitalisation", "transfert", "deces"] if s_ in sit_age.columns]
sit_age[ordre_sit].plot.bar(stacked=True, ax=axes[1], color=plt.cm.Set2.colors, rot=0)
axes[1].set_title("Devenir de la venue selon l'âge"); axes[1].set_ylabel("part des venues"); axes[1].legend(fontsize=8)

gr = P["gravite"]; gr = gr[gr["tranche_h"] == ""]       # ligne de repli : par classe d'âge × situation, toutes durées
g_age = gr.groupby(["classe_age", "gravite"])["part"].mean().unstack(fill_value=0).reindex(CLASSES_AGE)
g_age = g_age.div(g_age.sum(axis=1), axis=0)
g_age.plot.bar(stacked=True, ax=axes[2], colormap="Reds", rot=0)
axes[2].set_title("CCMU des hospitalisés selon l'âge"); axes[2].legend(title="CCMU", fontsize=8)
plt.tight_layout(); plt.show()
""")

md("""
### Combien de temps aux urgences, et le rôle de la tension

La tension du jour est le nombre de patients présents dans l'hôpital venus des urgences, au
relevé du matin ; ses bornes (terciles) définissent trois classes. Le tableau montre, pour les
hospitalisés de 75 ans et plus, la répartition de la durée de passage par classe de tension :
c'est là que la tension doit se voir (la masse glisse vers les tranches longues). Les courbes
donnent la tranche médiane par situation. Le supplément de rang 2 est l'attente supplémentaire
des patients qui partent dans un service exceptionnel, par classe de tension.
""")

code("""
ct = P["classes_tension"]; print("Bornes de la tension (présents venus des urgences) :"); print(ct.to_string(index=False))
du = P["durees_urgences"]
fin = du[(du["classe_tension"] != "")]
tab = fin[(fin["situation"] == "hospitalisation") & (fin["classe_age"] == "75+")].pivot_table(index="tranche_h", columns="classe_tension", values="part", aggfunc="sum").reindex(TRANCHES_H)
tab = tab[[c for c in ["basse", "moyenne", "haute"] if c in tab.columns]]
print("\\nHospitalisés de 75 ans et plus : part des venues par tranche de durée et classe de tension")
print((tab * 100).round(1).to_string())

def tranche_mediane(g):
    cum = g.set_index("tranche_h")["part"].reindex(TRANCHES_H).fillna(0).cumsum()
    return cum[cum >= 0.5].index[0] if (cum >= 0.5).any() else ""
fig, axes = plt.subplots(1, 2, figsize=(13, 4))
repli = du[(du["classe_tension"] == "") & (du["gravite"] == "")]       # repli situation × âge, toutes gravités et tensions
for sit, g in repli.groupby("situation"):
    med = g.groupby("classe_age").apply(tranche_mediane).reindex(CLASSES_AGE)
    axes[0].plot(CLASSES_AGE, [TRANCHES_H.index(m) if m in TRANCHES_H else np.nan for m in med], marker="o", label=sit)
axes[0].set_yticks(range(len(TRANCHES_H))); axes[0].set_yticklabels(TRANCHES_H); axes[0].set_ylabel("tranche médiane de durée (h)")
axes[0].set_title("Durée de passage médiane par situation et âge"); axes[0].legend(fontsize=8)
sup = P["supplement"].pivot_table(index="tranche_h", columns="classe_tension", values="part", aggfunc="sum").reindex(TRANCHES_H).fillna(0)
sup = sup[[c for c in ["basse", "moyenne", "haute"] if c in sup.columns]]
sup.plot.bar(ax=axes[1], rot=0, color=["#8ecae6", "#219ebc", "#023047"])
axes[1].set_title("Supplément d'attente avant un service de rang 2"); axes[1].set_xlabel("heures supplémentaires"); axes[1].set_ylabel("part des venues de rang 2")
plt.tight_layout(); plt.show()
""")

code("""
if FICHIER_TENSION.exists():
    tension = pd.read_parquet(FICHIER_TENSION); tension["date"] = pd.to_datetime(tension["date"])
    if "nohop" in tension.columns and tension["nohop"].nunique() > 1:
        tension = tension[tension["nohop"] == tension["nohop"].iloc[0]]
    fig, ax = plt.subplots(figsize=(13, 4))
    ax.plot(tension["date"], tension["tension"], lw=0.8, color="#2a6f97")
    ax.plot(tension["date"], tension["tension"].rolling(7, center=True).mean(), lw=2, color="#d1495b", label="moyenne glissante 7 jours")
    for _, b in ct.iterrows():
        if pd.notna(b["borne_sup"]):
            ax.axhline(b["borne_sup"], color="grey", ls="--", lw=0.8)
    ax.set_title("Tension quotidienne : patients présents venus des urgences (tirets : bornes des classes)"); ax.legend()
    plt.show()
else:
    print("Pas de série quotidienne de tension (fichier", FICHIER_TENSION.name, "absent) : seules les bornes sont connues.")
""")

md("""
## 4. Le casemix

### Ce qui entre par les urgences

`venues_urgences.csv` donne, pour les venues hospitalisées, la racine de GHM. En pondérant
les cases par les arrivées, on obtient la distribution des racines des hospitalisations
venues des urgences — le casemix « non programmé » de l'hôpital. Les libellés viennent de
`regroupements_ghm.csv`, qui permet aussi de regrouper par domaine d'activité (`da`).
""")

code("""
rgp = P["regroupements"]
libelle_racine = rgp.groupby("racine_ghm")["libelle"].first().str.replace(r",? niveau \\d$", "", regex=True)
da_racine = rgp.groupby("racine_ghm")["da"].first()
h = v[v["situation"] == "hospitalisation"].assign(w=w)
racines_urg = (h["part"] * h["w"]).groupby(h["racine_ghm"]).sum()
racines_urg = racines_urg / racines_urg.sum()
print(f"{len(racines_urg)} racines distinctes parmi les hospitalisations venues des urgences ; "
      f"{len(rgp['racine_ghm'].unique())} racines et {len(rgp)} GHM dans la nomenclature")
top = racines_urg.sort_values(ascending=False).head(20)
fig, axes = plt.subplots(1, 2, figsize=(15, 6), gridspec_kw={"width_ratios": [3, 2]})
axes[0].barh([f"{r} {libelle_racine.get(r, '')[:45]}" for r in top.index][::-1], top.values[::-1], color="#d1495b")
axes[0].set_xlabel("part des hospitalisations venues des urgences"); axes[0].set_title("Les 20 racines les plus fréquentes")
par_da = racines_urg.groupby(racines_urg.index.map(da_racine).fillna("?")).sum().sort_values()
axes[1].barh(par_da.index, par_da.values, color="#2a6f97"); axes[1].set_title("Par domaine d'activité (da)")
plt.tight_layout(); plt.show()
""")

md("""
### Ce qui entre en programmé

`casemix.csv` décrit, pour chaque spécialité, les racines et classes d'âge de ses entrées
programmées. On en tire, pour chaque service, ses trois racines principales et la part des
75 ans et plus.
""")

code("""
cm = P["casemix"]
lignes = []
for spec, g in cm.groupby("specialite"):
    par_racine = g.groupby("racine_ghm")["part"].sum().sort_values(ascending=False)
    lignes.append({"specialite": spec, "nb_racines": len(par_racine),
                   "racines_principales": " ; ".join(f"{r} {libelle_racine.get(r, '')[:30]} ({p:.0%})" for r, p in par_racine.head(3).items()),
                   "part_75+": round(g.loc[g["classe_age"] == "75+", "part"].sum(), 2),
                   "part_0-17": round(g.loc[g["classe_age"] == "0-17", "part"].sum(), 2)})
pd.set_option("display.max_colwidth", 120)
pd.DataFrame(lignes).sort_values("specialite").reset_index(drop=True)
""")

md("""
### Sévérité, durées et sorties

Trois tables par GHM, lues sur les racines les plus fréquentes aux urgences : la part des
niveaux de sévérité selon la classe d'âge (`niveaux.csv`, pondérée par la fréquence des
racines), la distribution des durées des séjours pour six racines (`durees_ghm.csv`, ligne
de repli par racine), et les modes de sortie par racine (`sorties.csv`, repli racine).
""")

code("""
niv = P["niveaux"]; niv = niv[niv["classe_age"] != ""]
niv = niv.assign(w=niv["racine_ghm"].map(racines_urg).fillna(0))
niv_age = (niv["part"] * niv["w"]).groupby([niv["classe_age"], niv["niveau"]]).sum().unstack(fill_value=0).reindex(CLASSES_AGE)
niv_age = niv_age.div(niv_age.sum(axis=1), axis=0)
dg = P["durees_ghm"]; dg = dg[dg["classe_age_duree"] == ""]            # repli : par GHM ou racine, tous âges
six = top.index[:6]
so = P["sorties"]; so = so[(so["niveau"] == "") & (so["classe_age"] == "")]
sorties_top = so[so["racine_ghm"].isin(top.index[:12])].pivot_table(index="racine_ghm", columns="mode_sortie", values="part", aggfunc="sum").reindex(top.index[:12]).fillna(0)

fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
niv_age.plot.bar(stacked=True, ax=axes[0], colormap="Blues", rot=0); axes[0].set_title("Niveau de sévérité selon l'âge\\n(racines pondérées par leur fréquence aux urgences)"); axes[0].legend(title="niveau", fontsize=8)
for r in six:
    d = dg[dg["ghm"] == r].copy()
    if d.empty: continue
    d["j"] = pd.to_numeric(d["duree_j"].replace("60+", "61")); d = d.sort_values("j")
    axes[1].plot(d["j"], d["part"].cumsum(), label=f"{r} {libelle_racine.get(r, '')[:20]}")
axes[1].set_xlim(0, 30); axes[1].set_xlabel("durée du séjour (jours)"); axes[1].set_ylabel("part cumulée"); axes[1].set_title("Durées de séjour, six racines fréquentes"); axes[1].legend(fontsize=7)
sorties_top.plot.barh(stacked=True, ax=axes[2], color=plt.cm.Set2.colors); axes[2].invert_yaxis(); axes[2].set_title("Mode de sortie des 12 racines les plus fréquentes"); axes[2].legend(fontsize=7)
plt.tight_layout(); plt.show()
""")

md("""
## 5. Les parcours

### Le premier service d'hospitalisation

`premier_service_racine.csv` est calculé sur plusieurs établissements ; comme le fera le
simulateur, on le **restreint aux services présents dans la structure**, on renormalise et
on recalcule les rangs. La carte montre, pour les 25 racines les plus fréquentes aux
urgences, la part de chaque service d'accueil (toutes classes d'âge : ligne de repli), un
point blanc marquant les services de rang 2. Une racine sans aucun service dans l'hôpital
(ligne vide) conduit au transfert.
""")

code("""
ps = P["premier_service"]
ps_restreint = restreindre_services(ps, services["service"], ["racine_ghm", "classe_age"])
# une racine dont il ne reste que des services de soins critiques n'a pas non plus de service d'accueil :
# la réanimation reçoit des patients qui ont une spécialité derrière eux, elle ne la remplace pas
conventionnels = set(services.loc[~services["type"].isin(["soins_critiques", "urgences"]), "service"])
avec_accueil = set(ps_restreint.loc[ps_restreint["specialite"].isin(conventionnels), "racine_ghm"])
perdues = set(ps["racine_ghm"]) - avec_accueil
print(f"{ps['specialite'].nunique()} spécialités dans la table multi-sites, {ps_restreint['specialite'].nunique()} présentes dans l'hôpital ; "
      f"{len(perdues)} racines sans service d'accueil conventionnel (→ transfert)" + (f" : {sorted(perdues)[:10]}" if perdues else ""))
r25 = top.index[:25]
rep = ps_restreint[(ps_restreint["classe_age"] == "") & ps_restreint["racine_ghm"].isin(r25)]
carte = rep.pivot_table(index="racine_ghm", columns="specialite", values="part", aggfunc="sum").reindex(r25).fillna(0)
rangs = rep.pivot_table(index="racine_ghm", columns="specialite", values="rang", aggfunc="min").reindex(r25)
carte = carte[carte.sum().sort_values(ascending=False).index]
fig, ax = plt.subplots(figsize=(0.55 * len(carte.columns) + 4, 0.4 * len(carte) + 1))
im = ax.imshow(carte.values, cmap="Blues", aspect="auto", vmin=0, vmax=1)
for i, r in enumerate(carte.index):
    for j, sp in enumerate(carte.columns):
        if pd.notna(rangs.loc[r, sp]) and rangs.loc[r, sp] == 2:
            ax.plot(j, i, "o", color="white", ms=4)
ax.set_xticks(range(len(carte.columns))); ax.set_xticklabels(carte.columns, rotation=90)
ax.set_yticks(range(len(carte))); ax.set_yticklabels([f"{r} {libelle_racine.get(r, '')[:35]}" for r in carte.index]); ax.grid(False)
ax.set_title("Premier service par racine, après restriction à la structure (point blanc : rang 2)"); plt.colorbar(im, ax=ax, fraction=0.02)
plt.show()
""")

md("""
### La suite du parcours

`table_passage.csv`, niveau `service` (le repli : la suite ne dépend que du service en cours),
après la même restriction : pour chaque service, la part des passages qui se terminent par la
sortie de l'hôpital et la répartition des mutations vers les autres services. Puis, de
`durees_passage.csv`, la durée d'un passage par spécialité : médiane et quartiles de la
spécialité (ligne de repli), et les médianes du niveau fin, un point par classe d'âge,
colorés selon le mode d'entrée — on y lit l'effet de l'âge et celui du mode d'entrée.
""")

code("""
tp = P["table_passage"]
niveau_service = tp[tp["niveau"] == "service"].copy()
suites = set(services["service"]) | {"sortie"}
niveau_service = niveau_service[niveau_service["suite"].isin(suites) & niveau_service["service"].isin(services["service"])]
niveau_service["part"] = niveau_service["part"] / niveau_service.groupby("service")["part"].transform("sum")
mat = niveau_service.pivot_table(index="service", columns="suite", values="part", aggfunc="sum").fillna(0)
mat = mat.reindex(sorted(mat.index))
colonnes = ["sortie"] + sorted(c for c in mat.columns if c != "sortie")
mat = mat.reindex(columns=colonnes, fill_value=0)
print("Part des passages qui se terminent par la sortie de l'hôpital (le reste : mutation vers un autre service) :")
print((mat["sortie"] * 100).round(0).astype(int).astype(str).add(" %").to_string())
fig, ax = plt.subplots(figsize=(0.5 * len(colonnes) + 3, 0.4 * len(mat) + 1))
im = ax.imshow(mat.values, cmap="Purples", aspect="auto", vmin=0, vmax=1)
ax.set_xticks(range(len(colonnes))); ax.set_xticklabels(colonnes, rotation=90); ax.set_yticks(range(len(mat))); ax.set_yticklabels(mat.index); ax.grid(False)
ax.set_xlabel("suite du parcours"); ax.set_ylabel("service en cours"); ax.set_title("Table de passage (niveau service), après restriction")
plt.colorbar(im, ax=ax, fraction=0.02); plt.show()
""")

code("""
# le repli (spécialité seule, toutes entrées) donne la médiane et les quartiles ; le niveau fin
# (spécialité × mode d'entrée × classe d'âge) donne un point par classe d'âge, coloré selon le mode
repli_sp = dp[(dp["classe_age"] == "") & (dp["mode_entree"] == "")].set_index("specialite")
fin_sp = dp[(dp["classe_age"] != "") & (dp["mode_entree"] != "")]
ordre = repli_sp["mediane_j"].sort_values(ascending=False).index
x = {sp: i for i, sp in enumerate(ordre)}
fig, ax = plt.subplots(figsize=(12, 4.5))
r = repli_sp.reindex(ordre)
ax.bar(range(len(ordre)), r["q3_j"] - r["q1_j"], bottom=r["q1_j"], color="#e0e0e0", width=0.6, label="quartiles (toutes entrées)")
ax.plot(range(len(ordre)), r["mediane_j"], "_", color="black", ms=18, mew=2, label="médiane (toutes entrées)")
for mode, couleur, dx in [("urgences", "#d1495b", -0.15), ("programme", "#2a6f97", 0.15)]:
    g = fin_sp[fin_sp["mode_entree"] == mode]
    ax.scatter([x[sp] + dx for sp in g["specialite"]], g["mediane_j"], s=18, color=couleur, label=f"médiane {mode}, par classe d'âge", zorder=3)
ax.set_xticks(range(len(ordre))); ax.set_xticklabels(ordre, rotation=90); ax.set_ylabel("jours")
ax.set_title("Durée d'un passage par spécialité"); ax.legend(fontsize=8)
plt.tight_layout(); plt.show()
""")

md("""
## Pour finir : ce que le simulateur devra reproduire

Les cibles du tableau de contrôle (`cibles.csv`) résument ce que les données disent de
l'hôpital. Le tableau ci-dessous les liste pour l'hôpital entier ; les cibles par service
(présents moyens, durée moyenne, part urgences) ont servi aux sections 1 et 2.
""")

code("""
c = P["cibles"]
c[c["service"] == ""][["indicateur", "classe_tension", "observe", "min", "max"]].reset_index(drop=True)
""")


# ---------------------------------------------------------------------------
# 6. Simuler, puis diagnostiquer les sorties
# ---------------------------------------------------------------------------

md("""
## 6. De bout en bout : simuler, puis regarder ce qui sort

Tout ce qui précède décrit l'hôpital *tel que les paramètres le disent*. On fait
maintenant tourner le simulateur sur ces mêmes paramètres et on regarde les tables
produites, avec deux questions en tête :

1. **les tables se tiennent-elles ?** — un séjour a bien ses RUM et ses mouvements,
   les dates s'enchaînent, les codes PMSI sont cohérents entre tables (c'est le même
   genre de contrôle que l'on ferait sur une extraction réelle) ;
2. **l'hôpital simulé ressemble-t-il à celui des paramètres ?** — entrées par jour,
   présents par service, tension, casemix.

### Lancer la simulation

La commande est celle du README : `python -m gen_admissions <dossier_parametres>
--sortie <dossier> --duree 365`. Elle simule une année « utile », précédée d'une
période de chauffe (l'hôpital vide se remplit) et suivie d'une marge (pour que les
séjours commencés en fin d'année se terminent) ; seuls les séjours et venues
entièrement dans la période utile sont écrits. Elle écrit dans le dossier de sortie :

- `rpu`, `rsa`, `rum`, `mouvements` en parquet et en CSV (format : `sorties/FORMAT.md`) ;
- `tension.parquet`, la série quotidienne de la tension simulée ;
- `resume.yaml` (graine, période, capacités, volumes) et `controle.csv`, le tableau de
  contrôle que la commande affiche aussi à l'écran.

La cellule ci-dessous lance la commande depuis le notebook, avec le même interpréteur
Python que le noyau. Le dossier de sortie est `sorties/essai` à la racine du dépôt pour
l'hôpital fictif, `<dossier_parametres>/sorties/essai` sinon (donc dans `prod/`, hors git).
Compter une trentaine de secondes pour un an ; `--sans-csv` l'accélère un peu.
""")

code("""
import subprocess, sys

GRAINE, DUREE = 42, 365
SORTIE_SIM = RACINE / "sorties" / "essai" if RACINE / "parametres" in DOSSIER.parents else DOSSIER / "sorties" / "essai"

commande = [sys.executable, "-m", "gen_admissions", str(DOSSIER), "--sortie", str(SORTIE_SIM), "--duree", str(DUREE), "--graine", str(GRAINE)]
print(" ".join(commande), "\\n")
res = subprocess.run(commande, cwd=RACINE, capture_output=True, text=True, encoding="utf-8")
print(res.stdout[-6000:])                 # la fin : le tableau de contrôle et le bilan
if res.returncode:
    print(res.stderr[-3000:])
""")

md("""
### Les tables produites

On relit les parquets. Les colonnes `sim_` (la vérité du simulateur) sont conservées :
elles servent plus bas à distinguer les entrées par les urgences des programmées
sans avoir à l'inférer, mais tout ce qui suit pourrait aussi se faire avec les seules
colonnes « réelles ».
""")

code("""
T = {nom: pd.read_parquet(SORTIE_SIM / f"{nom}.parquet") for nom in ("rpu", "rsa", "rum", "mouvements", "tension")}
RESUME = yaml.safe_load(open(SORTIE_SIM / "resume.yaml", encoding="utf-8"))
CONTROLE = pd.read_csv(SORTIE_SIM / "controle.csv")
rpu, rsa, rum, mvt = T["rpu"], T["rsa"], T["rum"], T["mouvements"]
DEBUT, FIN = pd.Timestamp(RESUME["periode"]["debut"]), pd.Timestamp(RESUME["periode"]["fin"])
print({k: len(v) for k, v in T.items()})
rsa.head(3)
""")

md("""
### Les tables se tiennent-elles ? Tests de jointure et de cohérence

Chaque ligne du tableau est un test ; `defauts` compte les lignes qui le violent. Sur
une sortie du simulateur, tout doit être à zéro : ces tests sont la définition même du
format. Les mêmes tests, lancés sur une extraction réelle, ne donnent jamais zéro
partout — c'est précisément l'écart entre le monde propre du simulateur et les données
réelles (séjours à cheval sur la période, RUM non contigus, mouvements sans RUM…).
""")

code("""
def tests_coherence(rpu, rsa, rum, mvt):
    t = []
    def test(nom, masque_defauts, detail=""):
        n = int(pd.Series(masque_defauts).sum())
        t.append({"test": nom, "defauts": n, "ok": n == 0, "detail": detail})

    # --- identifiants et jointures
    test("rsa : nas unique", rsa["nas"].duplicated())
    test("rpu : no_visite unique", rpu["no_visite"].duplicated())
    test("rum -> rsa : chaque RUM a son séjour", ~rum["nas"].isin(rsa["nas"]))
    test("mouvements -> rsa : chaque mouvement a son séjour", ~mvt["nas"].isin(rsa["nas"]))
    test("rsa -> rum : chaque séjour a au moins un RUM", ~rsa["nas"].isin(rum["nas"]))
    test("rsa -> mouvements : chaque séjour a au moins un mouvement", ~rsa["nas"].isin(mvt["nas"]))
    test("rpu -> rsa : la venue hospitalisée pointe un séjour existant", rpu["nas"].notna() & ~rpu["nas"].isin(rsa["nas"]))
    test("rsa -> rpu : le séjour venu des urgences pointe une venue existante", rsa["no_visite"].notna() & ~rsa["no_visite"].isin(rpu["no_visite"]))
    test("rsa : venu des urgences (prov=5) <=> no_visite renseigné", (rsa["prov"] == "5") != rsa["no_visite"].notna())
    test("rpu : mode_sortie 6 (mutation) <=> nas renseigné", (rpu["mode_sortie"] == "6") != rpu["nas"].notna())
    ipp_rum = rum[["nas", "ipp"]].drop_duplicates().merge(rsa[["nas", "ipp"]], on="nas", suffixes=("", "_rsa"))
    test("ipp identique entre rum et rsa", ipp_rum["ipp"] != ipp_rum["ipp_rsa"])

    # --- comptages annoncés dans le séjour
    n_rum = rum.groupby("nas").size().rename("n")
    j = rsa.set_index("nas").join(n_rum)
    test("rsa.nb_rum = nombre de RUM", j["nb_rum"] != j["n"])
    n_mvt = mvt.groupby("nas").size().rename("n")
    j = mvt.set_index("nas")[["nbmvt"]].join(n_mvt)
    test("mouvements.nbmvt = nombre de mouvements du séjour", j["nbmvt"] != j["n"])

    # --- enchaînement des RUM dans le temps
    r = rum.sort_values(["nas", "norum"]).copy()
    r["rang_attendu"] = r.groupby("nas").cumcount() + 1
    test("rum : norum consécutifs à partir de 1", r["norum"] != r["rang_attendu"])
    r["fin_precedent"] = r.groupby("nas")["d8soue"].shift()
    test("rum : chaque RUM commence quand le précédent finit", r["fin_precedent"].notna() & (r["d8eeue"] != r["fin_precedent"]))
    test("rum : d8eeue <= d8soue", r["d8eeue"] > r["d8soue"])
    bornes = r.groupby("nas").agg(premier=("d8eeue", "first"), dernier=("d8soue", "last")).join(rsa.set_index("nas")[["d8eeue", "d8soue"]])
    test("rsa.d8eeue = entrée du premier RUM", bornes["premier"] != bornes["d8eeue"])
    test("rsa.d8soue = sortie du dernier RUM", bornes["dernier"] != bornes["d8soue"])
    test("rsa : d8eeue et d8soue dans la période utile", (rsa["d8eeue"] < DEBUT) | (rsa["d8soue"] > FIN))
    test("rum.noseqrum désigne un RUM existant du séjour", ~r.set_index(["nas", "noseqrum"]).index.isin(r.set_index(["nas", "norum"]).index))

    # --- codes PMSI entre tables
    dernier = r.groupby("nas").last().join(rsa.set_index("nas")[["schpmsi"]])
    test("dernier RUM : mdsoue = schpmsi du séjour", dernier["mdsoue"] != dernier["schpmsi"])
    test("RUM intermédiaires : mdsoue = 6 (mutation)", (r["norum"] < r.groupby("nas")["norum"].transform("max")) & (r["mdsoue"] != "6"))
    premier = r[r["norum"] == 1].set_index("nas").join(rsa.set_index("nas")[["prov", "echpmsi"]])
    test("premier RUM : mdeeue = echpmsi du séjour", premier["mdeeue"] != premier["echpmsi"])
    test("premier RUM : cdpu = 5 si et seulement si prov = 5", (premier["cdpu"] == "5") != (premier["prov"] == "5"))
    test("RUM suivants : cdpu vide", (r["norum"] > 1) & (r["cdpu"] != ""))

    # --- urgences et séjour
    # sans UHCD, le séjour commence quand la venue finit ; avec UHCD, il commence à l'entrée en UHCD, pendant la venue
    j = rpu.dropna(subset=["nas"]).merge(rsa[["nas", "d8eeue"]], on="nas")
    test("hospitalisé sans UHCD : l'entrée du séjour est la sortie des urgences", (j["sim_uhcd"] == "non") & (j["sortie"] != j["d8eeue"]))
    test("hospitalisé via l'UHCD : l'entrée du séjour est pendant la venue", (j["sim_uhcd"] == "oui") & ~j["d8eeue"].between(j["entree"], j["sortie"]))
    test("rpu : entree <= sortie", rpu["entree"] > rpu["sortie"])

    # --- mouvements dans leur RUM
    j = mvt.merge(rum[["nas", "spe_uma", "d8eeue", "d8soue"]], on=["nas", "spe_uma"], suffixes=("", "_rum"))
    dans = (j["d8eeue"] >= j["d8eeue_rum"]) & (j["d8soue"] <= j["d8soue_rum"])
    test("chaque mouvement est inclus dans un RUM de même spécialité", ~dans.groupby([j["nas"], j["nomvt"]]).any())
    return pd.DataFrame(t)

TESTS = tests_coherence(rpu, rsa, rum, mvt)
print(f"{TESTS['ok'].sum()} tests sur {len(TESTS)} sans défaut")
TESTS.style.map(lambda ok: "color: green" if ok else "color: red; font-weight: bold", subset=["ok"])
""")

md("""
### Les entrées, jour après jour

Trois rythmes se superposent : la saison (le mois), la semaine (le programmé s'arrête le
week-end, les urgences non) et le bruit poissonnien d'un jour à l'autre. Le trait épais
est la moyenne glissante sur 7 jours, qui efface la semaine pour laisser voir la saison.
""")

code("""
jour = lambda s: s.dt.normalize()
entrees = pd.DataFrame({
    "venues aux urgences": rpu.groupby(jour(rpu["entree"])).size(),
    "séjours entrés par les urgences": rsa[rsa["prov"] == "5"].groupby(jour(rsa["d8eeue"])).size(),
    "séjours programmés": rsa[rsa["prov"] != "5"].groupby(jour(rsa["d8eeue"])).size(),
}).reindex(pd.date_range(DEBUT, FIN - pd.Timedelta(days=1)), fill_value=0)

fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
for ax, cols in zip(axes, [["venues aux urgences"], ["séjours entrés par les urgences", "séjours programmés"]]):
    for c in cols:
        trait, = ax.plot(entrees.index, entrees[c].rolling(7, center=True).mean(), lw=2, label=c)
        ax.plot(entrees.index, entrees[c], lw=0.6, alpha=0.4, color=trait.get_color())
    ax.legend(loc="upper right"); ax.set_ylabel("par jour")
axes[0].set_title("Entrées par jour sur l'année simulée")
plt.tight_layout(); plt.show()

semaine = entrees.groupby(entrees.index.dayofweek).mean().round(1)
semaine.index = JOURS
print("moyenne par jour de semaine :"); display(semaine.T)
""")

md("""
### Les présents par jour et par service

Le nombre de présents se lit dans les **mouvements** : à un instant donné, un patient est
présent dans un service s'il y a un mouvement de ce service qui a commencé avant et qui
finit après. On relève la photographie **chaque matin à 8 h**, comme la tension du
simulateur. On compare ensuite la moyenne des présents à la capacité (lits ouverts,
dans `resume.yaml`) et aux présents visés des paramètres.
""")

code("""
def presents_a(mvt, instants):
    \"\"\"Présents par service à chaque instant : nb de mouvements commencés avant et finis après.\"\"\"
    out = {}
    for sp, g in mvt.groupby("spe_uma"):
        e, s = np.sort(g["d8eeue"].values), np.sort(g["d8soue"].values)
        out[sp] = np.searchsorted(e, instants.values, side="right") - np.searchsorted(s, instants.values, side="right")
    return pd.DataFrame(out, index=instants)

MATINS = pd.date_range(DEBUT, FIN - pd.Timedelta(days=1)) + pd.Timedelta(hours=8)
PRESENTS = presents_a(mvt, MATINS)
CAPACITES = pd.Series(RESUME["capacites"])
URG = S["urgences"]["service"]
conventionnels = [c for c in PRESENTS.columns if c != URG and c not in {code for code, sv in S["services"].items() if sv.get("soins_critiques")}]

fig, ax = plt.subplots(figsize=(11, 4))
ax.plot(PRESENTS.index, PRESENTS[conventionnels].sum(axis=1), lw=1, label="présents à 8 h, services conventionnels")
ax.axhline(CAPACITES[conventionnels].sum(), color="black", ls="--", lw=1, label="lits ouverts")
ax.legend(); ax.set_title("Présents dans les services conventionnels, chaque matin"); plt.tight_layout(); plt.show()

bilan_services = pd.DataFrame({"presents_moyens": PRESENTS.mean().round(1), "p95": PRESENTS.quantile(0.95).round(0), "lits_ouverts": CAPACITES})
bilan_services["occupation"] = (bilan_services["presents_moyens"] / bilan_services["lits_ouverts"]).round(2)
cible = P["cibles"].query("indicateur == 'presents_moyens'").set_index("service")["observe"]
bilan_services["presents_vises"] = cible.reindex(bilan_services.index).round(1)
bilan_services["ecart_relatif"] = ((bilan_services["presents_moyens"] - bilan_services["presents_vises"]) / bilan_services["presents_vises"]).round(2)
display(bilan_services.sort_values("occupation", ascending=False))

fig, ax = plt.subplots(figsize=(11, 4))
ordre = bilan_services.drop(index=URG, errors="ignore").sort_values("lits_ouverts", ascending=False)
x = np.arange(len(ordre))
ax.bar(x - 0.2, ordre["lits_ouverts"], 0.4, color="lightgrey", label="lits ouverts")
ax.bar(x + 0.2, ordre["presents_moyens"], 0.4, color="#2a6f97", label="présents moyens simulés")
ax.scatter(x + 0.2, ordre["presents_vises"], color="#d1495b", zorder=3, s=20, label="présents visés (paramètres)")
ax.set_xticks(x); ax.set_xticklabels(ordre.index, rotation=90); ax.legend(); ax.set_title("Lits, présents simulés et présents visés, par service")
plt.tight_layout(); plt.show()
""")

md("""
### La tension simulée

La tension est le taux d'occupation des lits conventionnels relevé chaque matin ; ses
classes (bornes dans `classes_tension.csv`) pilotent les tirages des urgences. On
vérifie que la série simulée visite bien les classes dans des proportions comparables
à la référence (section 4 pour la série réelle, si elle est disponible).
""")

code("""
tension = T["tension"].assign(date=lambda d: pd.to_datetime(d["date"]))
fig, ax = plt.subplots(figsize=(11, 3.5))
ax.plot(tension["date"], tension["tension"], lw=1)
bornes = P["classes_tension"].fillna({"borne_inf": tension["tension"].min(), "borne_sup": tension["tension"].max()})
for _, b in bornes.iterrows():
    ax.axhspan(b["borne_inf"], b["borne_sup"], alpha=0.06, color="C1")
    ax.text(tension["date"].iloc[0], (b["borne_inf"] + b["borne_sup"]) / 2, b["classe"], fontsize=8, va="center")
ax.set_title("Tension simulée (occupation des lits conventionnels à 8 h)"); plt.tight_layout(); plt.show()
print("part des jours par classe de tension :")
display(tension["classe_tension"].value_counts(normalize=True).round(3).rename("part").to_frame().T)
""")

md("""
### Le casemix

Ce que l'hôpital simulé a pris en charge : les racines de GHM les plus fréquentes, la
répartition des niveaux de sévérité, la pyramide des âges, et la durée moyenne de séjour
par spécialité du RUM principal. À rapprocher de la section 3 (le casemix *des paramètres*).
""")

code("""
rsa_ = rsa.assign(racine=rsa["ghm"].str[:5], niveau=rsa["gpcompx"], origine=np.where(rsa["prov"] == "5", "urgences", "programme"))
lib = P["regroupements"].assign(lib=lambda d: d["libelle"].str.replace(r", niveau \\d$", "", regex=True)).drop_duplicates("racine_ghm").set_index("racine_ghm")["lib"] if "libelle" in P["regroupements"].columns else None

top = rsa_.groupby(["racine", "origine"]).size().unstack(fill_value=0)
top["total"] = top.sum(axis=1)
top = top.sort_values("total", ascending=False).head(20)
if lib is not None:
    top.insert(0, "libelle", lib.reindex(top.index).str.slice(0, 60))
display(top)

fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
rsa_["niveau"].value_counts().sort_index().plot.bar(ax=axes[0], color="#2a6f97", title="Niveaux de sévérité (gpcompx)")
ages = pd.cut(rsa_["agean"], bins=[-1, 17, 64, 74, 200], labels=CLASSES_AGE)
pd.crosstab(ages, rsa_["sexe"]).plot.bar(ax=axes[1], title="Séjours par classe d'âge et sexe (1 H, 2 F)")
pd.crosstab(ages, rsa_["origine"], normalize="index").plot.bar(ax=axes[2], stacked=True, title="Part urgences / programme par âge")
for ax in axes: ax.set_xlabel("")
plt.tight_layout(); plt.show()

principal = rum[rum["norum"] == rum["noseqrum"]][["nas", "spe_uma"]].merge(rsa_[["nas", "duree", "origine"]], on="nas")
dms = principal.groupby(["spe_uma", "origine"])["duree"].mean().unstack().round(1)
dms["n_sejours"] = principal.groupby("spe_uma").size()
print("durée de séjour moyenne (nuitées) selon la spécialité du RUM principal :")
display(dms.sort_values("n_sejours", ascending=False))

retours = rsa_.groupby("ipp").size()
print(f"{len(retours)} personnes pour {len(rsa_)} séjours ; {(retours > 1).mean():.1%} des personnes ont plusieurs séjours dans l'année")
""")

md("""
### Pour finir : le tableau de contrôle

`controle.csv` reprend, indicateur par indicateur, la cible issue des paramètres, la
valeur recalculée sur les tables simulées et la fourchette admise. Les lignes hors
fourchette sont celles sur lesquelles le simulateur reste à régler — ou celles dont la
cible repose sur un petit effectif.
""")

code("""
hors = CONTROLE[CONTROLE["dans_fourchette"] == False].sort_values("ecart_relatif", key=abs, ascending=False)
print(f"{int(CONTROLE['dans_fourchette'].sum())} indicateurs sur {CONTROLE['dans_fourchette'].notna().sum()} dans leur fourchette ; hors fourchette :")
hors[["indicateur", "service", "observe", "simule", "min", "max", "ecart_relatif"]].reset_index(drop=True)
""")


def main() -> None:
    nb = new_notebook(cells=CELLULES, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                                                  "language_info": {"name": "python"}})
    nbformat.write(nb, SORTIE)
    print(len(nb.cells), "cellules ->", SORTIE)


if __name__ == "__main__":
    main()
