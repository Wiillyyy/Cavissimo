"""Parcours HTTP réels avec une base temporaire, jamais la cave personnelle."""

import tempfile
import unittest
from pathlib import Path

from app import creer_app
import database


class ScenarioWeb(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self.dossier.cleanup)
        self.chemin = str(Path(self.dossier.name) / "test.sqlite3")
        self.config = {"TESTING": True, "DATABASE": self.chemin, "SECRET_KEY": "cle-tests"}
        self.app = creer_app(self.config)
        self.client = self.app.test_client()
        self.client.get("/connexion")

    def envoyer(self, url, donnees, client=None):
        client = client or self.client
        client.get("/connexion")
        with client.session_transaction() as session:
            jeton = session["csrf_token"]
        return client.post(url, data={**donnees, "csrf_token": jeton}, follow_redirects=True)

    def inscrire(self, identifiant="William", client=None):
        return self.envoyer("/inscription", {
            "identifiant": identifiant, "email": identifiant.lower() + "@exemple.fr",
            "mot_de_passe": "une phrase de test 123", "confirmation": "une phrase de test 123",
        }, client)


class TestSite(ScenarioWeb):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.inscrire().status_code, 200)

    def test_cinq_emplacements_et_limite_par_cave(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertEqual(html.count('class="cellar-slot is-vacant"'), 5)
        for numero in range(5):
            reponse = self.envoyer("/etageres", {"nom": f"Étagère {numero + 1}", "capacite": "12"})
            self.assertEqual(reponse.status_code, 200)
            html = reponse.get_data(as_text=True)
            self.assertEqual(html.count('class="cellar-slot is-built"'), numero + 1)
            self.assertEqual(html.count('class="cellar-slot is-vacant"'), 4 - numero)
        refus = self.envoyer("/etageres", {"nom": "Une sixième", "capacite": "12"})
        self.assertEqual(refus.status_code, 400)
        self.assertIn("au maximum 5 étagères", refus.get_data(as_text=True))
        self.assertEqual(len(database.consulter_cave(self.chemin, 1)["etageres"]), 5)
        autre = self.app.test_client()
        self.inscrire("Bastien", autre)
        self.assertEqual(self.envoyer("/etageres", {"nom": "La première", "capacite": "12"}, autre).status_code, 200)

    def preparer_cave(self):
        self.assertEqual(self.envoyer("/etageres", {"nom": "Mes rouges", "capacite": "3"}).status_code, 200)
        self.assertEqual(self.envoyer("/vins", {
            "domaine": "Domaine des amis", "nom_cuvee": "Partage", "type": "rouge",
            "millesime": "2022", "region": "Savoie",
        }).status_code, 200)

    def test_cave_vide_et_fichiers_statiques(self):
        page = self.client.get("/")
        self.assertIn("Créez votre première étagère", page.get_data(as_text=True))
        for fichier in ("css/style.css", "js/main.js"):
            with self.client.get("/static/" + fichier) as reponse:
                self.assertEqual(reponse.status_code, 200)

    def test_modifier_capacite_respecte_stock_et_persiste(self):
        self.preparer_cave()
        self.envoyer("/bouteilles", {"etagere_id": "1", "vin_id": "1", "quantite": "2", "prix_achat": "12.50"})
        refus = self.envoyer("/etageres/1/capacite", {"capacite": "1"})
        self.assertEqual(refus.status_code, 400)
        self.assertEqual(database.consulter_cave(self.chemin, 1)["etageres"][0]["capacite_max"], 3)
        for capacite in ("0", "-1", "2.5", "abc"):
            self.assertEqual(self.envoyer("/etageres/1/capacite", {"capacite": capacite}).status_code, 400)
        for capacite in (2, 5, 20):
            self.assertEqual(self.envoyer("/etageres/1/capacite", {"capacite": str(capacite)}).status_code, 200)
            cave = database.consulter_cave(self.chemin, 1)
            self.assertEqual(cave["etageres"][0]["capacite_max"], capacite)
            self.assertEqual(cave["stats"]["capacite"], capacite)
            self.assertEqual(cave["stats"]["bouteilles"], 2)
        nouvelle_app = creer_app(self.config)
        self.assertEqual(database.consulter_cave(nouvelle_app.config["DATABASE"], 1)["stats"]["capacite"], 20)

    def test_apercu_adapte_aux_capacites(self):
        for capacite in (1, 5, 6, 10, 20):
            self.envoyer("/etageres", {"nom": f"Capacité {capacite}", "capacite": str(capacite)})
        html = self.client.get("/").get_data(as_text=True)
        etageres = html.split('<article class="cellar-slot is-built">')[1:]
        for fragment, capacite in zip(etageres, (1, 5, 6, 10, 20)):
            fragment = fragment.split('</article>', 1)[0]
            self.assertEqual(fragment.count('class="rack-bottle '), min(capacite, 10))
            self.assertEqual(fragment.count('class="rack-tier"'), 1 if capacite <= 5 else 2)

    def test_modifier_capacite_verifie_proprietaire_et_csrf(self):
        self.preparer_cave()
        self.assertEqual(self.client.post("/etageres/1/capacite", data={"capacite": "5"}).status_code, 400)
        autre = self.app.test_client()
        self.inscrire("Bastien", autre)
        for etagere_id in (1, 999):
            self.assertEqual(self.envoyer(f"/etageres/{etagere_id}/capacite", {"capacite": "5"}, autre).status_code, 404)
        self.assertEqual(database.consulter_cave(self.chemin, 1)["etageres"][0]["capacite_max"], 3)
        visiteur = self.app.test_client()
        self.assertEqual(visiteur.post("/etageres/1/capacite", data={"capacite": "5"}).status_code, 302)

    def test_ajout_multiple_refus_atomique_et_persistance(self):
        self.preparer_cave()
        donnees = {"etagere_id": "1", "vin_id": "1", "quantite": "2", "prix_achat": "12,50"}
        self.assertEqual(self.envoyer("/bouteilles", donnees).status_code, 200)
        refus = self.envoyer("/bouteilles", donnees)
        self.assertEqual(refus.status_code, 400)
        self.assertIn("Aucun ajout effectué", refus.get_data(as_text=True))
        # Nouvelle application et nouvelle connexion : les données survivent.
        nouvelle_app = creer_app(self.config)
        nouveau_client = nouvelle_app.test_client()
        self.assertEqual(self.envoyer("/connexion", {
            "identifiant": "William", "mot_de_passe": "une phrase de test 123",
        }, nouveau_client).status_code, 200)
        cave = database.consulter_cave(self.chemin, 1)
        self.assertEqual(cave["stats"]["bouteilles"], 2)
        self.assertEqual(cave["stats"]["references"], 1)
        self.assertEqual(cave["stats"]["valeur_centimes"], 2500)
        self.assertEqual(cave["etageres"][0]["occupation"], 2)
        self.assertNotEqual(cave["bouteilles"][0]["id"], cave["bouteilles"][1]["id"])

    def test_supprimer_etagere_vide_libere_un_emplacement(self):
        for numero in range(5):
            self.envoyer("/etageres", {"nom": f"Étagère {numero + 1}", "capacite": "10"})
        reponse = self.envoyer("/etageres/1/supprimer", {"confirmation": "oui"})
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.get_data(as_text=True).count('class="cellar-slot is-vacant"'), 1)
        cave = database.consulter_cave(self.chemin, 1)
        self.assertEqual(cave["stats"]["etageres"], 4)
        self.assertEqual(cave["stats"]["capacite"], 40)
        self.assertNotIn(1, [e["id"] for e in cave["etageres"]])
        nouvelle_app = creer_app(self.config)
        self.assertEqual(database.consulter_cave(nouvelle_app.config["DATABASE"], 1)["stats"]["etageres"], 4)
        self.assertEqual(self.envoyer("/etageres/1/supprimer", {"confirmation": "oui"}).status_code, 404)
        self.assertEqual(self.envoyer("/etageres", {"nom": "Étagère 1", "capacite": "5"}).status_code, 200)
        self.assertEqual(database.consulter_cave(self.chemin, 1)["stats"]["etageres"], 5)

    def test_supprimer_etagere_non_vide_refuse_sans_perte(self):
        self.preparer_cave()
        self.envoyer("/bouteilles", {"etagere_id": "1", "vin_id": "1", "quantite": "2", "prix_achat": "12.50"})
        reponse = self.envoyer("/etageres/1/supprimer", {"confirmation": "oui"})
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("non vide", reponse.get_data(as_text=True))
        cave = database.consulter_cave(self.chemin, 1)
        self.assertEqual(cave["stats"]["etageres"], 1)
        self.assertEqual(cave["stats"]["bouteilles"], 2)
        self.assertEqual(cave["stats"]["valeur_centimes"], 2500)
        self.assertEqual(len(cave["vins"]), 1)

    def test_supprimer_etagere_protege_acces_csrf_et_confirmation(self):
        self.preparer_cave()
        self.assertEqual(self.client.get("/etageres/1/supprimer").status_code, 405)
        self.assertEqual(self.client.post("/etageres/1/supprimer", data={"confirmation": "oui"}).status_code, 400)
        self.assertEqual(self.envoyer("/etageres/1/supprimer", {}).status_code, 400)
        autre = self.app.test_client()
        self.inscrire("Bastien", autre)
        for etagere_id in (1, 999):
            self.assertEqual(self.envoyer(f"/etageres/{etagere_id}/supprimer", {"confirmation": "oui"}, autre).status_code, 404)
        visiteur = self.app.test_client()
        self.assertEqual(visiteur.post("/etageres/1/supprimer", data={"confirmation": "oui"}).status_code, 302)
        self.assertEqual(database.consulter_cave(self.chemin, 1)["stats"]["etageres"], 1)

    def test_doublons_normalises(self):
        self.preparer_cave()
        self.assertEqual(self.envoyer("/etageres", {"nom": "  MES   ROUGES ", "capacite": "10"}).status_code, 400)
        reponse = self.envoyer("/vins", {
            "domaine": " DOMAINE  DES AMIS ", "nom_cuvee": " partage ", "type": "rouge",
            "millesime": "2022", "region": " SAVOIE ",
        })
        self.assertEqual(reponse.status_code, 200)
        cave = database.consulter_cave(self.chemin, 1)
        self.assertEqual(len(cave["vins"]), 1)
        self.assertEqual(len(cave["etageres"]), 1)

    def test_retirer_un_exemplaire_parmi_cinq(self):
        self.envoyer("/etageres", {"nom": "Mes rouges", "capacite": "10"})
        self.envoyer("/vins", {"domaine": "Domaine", "nom_cuvee": "Cuvée", "type": "rouge", "millesime": "2022", "region": "Savoie"})
        self.envoyer("/bouteilles", {"etagere_id": "1", "vin_id": "1", "quantite": "5", "prix_achat": "12.50"})
        reponse = self.envoyer("/bouteilles/3/retirer", {"confirmation": "oui"})
        self.assertEqual(reponse.status_code, 200)
        cave = database.consulter_cave(self.chemin, 1)
        self.assertEqual(cave["stats"]["bouteilles"], 4)
        self.assertEqual(cave["etageres"][0]["occupation"], 4)
        self.assertEqual(cave["stats"]["valeur_centimes"], 5000)
        self.assertEqual({b["id"] for b in cave["bouteilles"]}, {1, 2, 4, 5})
        self.assertEqual(len(cave["vins"]), 1)
        self.assertEqual(self.envoyer("/bouteilles/3/retirer", {"confirmation": "oui"}).status_code, 404)

    def test_retirer_derniere_bouteille_permet_supprimer_etagere(self):
        self.preparer_cave()
        self.envoyer("/bouteilles", {"etagere_id": "1", "vin_id": "1", "quantite": "1", "prix_achat": "12.50"})
        self.assertEqual(self.envoyer("/bouteilles/1/retirer", {"confirmation": "oui"}).status_code, 200)
        cave = database.consulter_cave(self.chemin, 1)
        self.assertEqual(cave["stats"]["bouteilles"], 0)
        self.assertEqual(cave["etageres"][0]["occupation"], 0)
        self.assertEqual(cave["stats"]["references"], 0)
        self.assertEqual(cave["stats"]["valeur_centimes"], 0)
        self.assertEqual(len(cave["vins"]), 1)
        self.assertEqual(self.envoyer("/etageres/1/supprimer", {"confirmation": "oui"}).status_code, 200)
        nouvelle_app = creer_app(self.config)
        cave = database.consulter_cave(nouvelle_app.config["DATABASE"], 1)
        self.assertEqual(cave["stats"]["etageres"], 0)
        self.assertEqual(len(cave["vins"]), 1)

    def test_retrait_verifie_proprietaire_csrf_confirmation(self):
        self.preparer_cave()
        self.envoyer("/bouteilles", {"etagere_id": "1", "vin_id": "1", "quantite": "1", "prix_achat": "12.50"})
        self.assertEqual(self.client.get("/bouteilles/1/retirer").status_code, 405)
        self.assertEqual(self.client.post("/bouteilles/1/retirer", data={"confirmation": "oui"}).status_code, 400)
        self.assertEqual(self.envoyer("/bouteilles/1/retirer", {}).status_code, 400)
        autre = self.app.test_client()
        self.inscrire("Bastien", autre)
        for bouteille_id in (1, 999):
            self.assertEqual(self.envoyer(f"/bouteilles/{bouteille_id}/retirer", {"confirmation": "oui"}, autre).status_code, 404)
        visiteur = self.app.test_client()
        self.assertEqual(visiteur.post("/bouteilles/1/retirer", data={"confirmation": "oui"}).status_code, 302)
        self.assertEqual(database.consulter_cave(self.chemin, 1)["stats"]["bouteilles"], 1)

    def test_donnees_invalides_sans_ajout(self):
        self.preparer_cave()
        donnees = {"etagere_id": "1", "vin_id": "1", "quantite": "1", "prix_achat": "12.50"}
        for champ, valeur in [("prix_achat", "NaN"), ("prix_achat", "-1"),
                              ("prix_achat", "1.001"), ("quantite", "0"),
                              ("quantite", "1.5"), ("vin_id", "99")]:
            with self.subTest(champ=champ, valeur=valeur):
                self.assertEqual(self.envoyer("/bouteilles", {**donnees, champ: valeur}).status_code, 400)
        self.assertEqual(database.consulter_cave(self.chemin, 1)["stats"]["bouteilles"], 0)
        for capacite in ("0", "-1", "abc", "2.5"):
            self.assertEqual(self.envoyer("/etageres", {"nom": "Invalide", "capacite": capacite}).status_code, 400)

    def test_renommer_etagere_actualise_stock_et_persiste(self):
        self.preparer_cave()
        self.envoyer("/bouteilles", {"etagere_id": "1", "vin_id": "1", "quantite": "2", "prix_achat": "12.50"})
        reponse = self.envoyer("/etageres/1/modifier", {"nom": "  Ma réserve  ", "capacite": "5"})
        self.assertEqual(reponse.status_code, 200)
        cave = database.consulter_cave(self.chemin, 1)
        self.assertEqual(cave["etageres"][0]["nom"], "Ma réserve")
        self.assertEqual(cave["etageres"][0]["capacite_max"], 5)
        self.assertEqual({b["etagere"] for b in cave["bouteilles"]}, {"Ma réserve"})
        self.assertEqual(cave["stats"]["bouteilles"], 2)
        nouvelle_app = creer_app(self.config)
        self.assertEqual(database.consulter_cave(nouvelle_app.config["DATABASE"], 1)["etageres"][0]["nom"], "Ma réserve")

    def test_renommer_refuse_doublon_et_modification_partielle(self):
        self.preparer_cave()
        self.envoyer("/etageres", {"nom": "Mes blancs", "capacite": "10"})
        self.envoyer("/bouteilles", {"etagere_id": "1", "vin_id": "1", "quantite": "2", "prix_achat": "12.50"})
        for nom, capacite in [(" MES  BLANCS ", "20"), (" ", "20"), ("x" * 101, "20"),
                              ("Nouveau nom", "1"), ("Nouveau nom", "0")]:
            with self.subTest(nom=nom, capacite=capacite):
                self.assertEqual(self.envoyer("/etageres/1/modifier", {"nom": nom, "capacite": capacite}).status_code, 400)
                cave = database.consulter_cave(self.chemin, 1)
                self.assertEqual(cave["etageres"][0]["nom"], "Mes rouges")
                self.assertEqual(cave["etageres"][0]["capacite_max"], 3)
        self.assertEqual(self.envoyer("/etageres/1/modifier", {"nom": "MES ROUGES", "capacite": "3"}).status_code, 200)

    def test_renommer_verifie_acces_et_csrf(self):
        self.preparer_cave()
        donnees = {"nom": "Nouveau", "capacite": "5"}
        self.assertEqual(self.client.post("/etageres/1/modifier", data=donnees).status_code, 400)
        self.assertEqual(self.client.get("/etageres/1/modifier").status_code, 405)
        autre = self.app.test_client()
        self.inscrire("Bastien", autre)
        for etagere_id in (1, 999):
            self.assertEqual(self.envoyer(f"/etageres/{etagere_id}/modifier", donnees, autre).status_code, 404)
        visiteur = self.app.test_client()
        self.assertEqual(visiteur.post("/etageres/1/modifier", data=donnees).status_code, 302)
        self.assertEqual(database.consulter_cave(self.chemin, 1)["etageres"][0]["nom"], "Mes rouges")

    def test_modifier_prix_un_seul_exemplaire_et_total(self):
        self.preparer_cave()
        self.envoyer("/bouteilles", {"etagere_id": "1", "vin_id": "1", "quantite": "2", "prix_achat": "12.50"})
        avant = database.consulter_cave(self.chemin, 1)["bouteilles"]
        self.assertEqual(self.envoyer("/bouteilles/1/prix", {"prix_achat": "19,99"}).status_code, 200)
        cave = database.consulter_cave(self.chemin, 1)
        prix = {b["id"]: b["prix_centimes"] for b in cave["bouteilles"]}
        self.assertEqual(prix, {1: 1999, 2: 1250})
        self.assertEqual(cave["stats"]["valeur_centimes"], 3249)
        self.assertEqual(cave["stats"]["bouteilles"], 2)
        self.assertEqual(cave["etageres"][0]["occupation"], 2)
        self.assertEqual(len(cave["vins"]), 1)
        self.assertEqual([b["date_ajout"] for b in cave["bouteilles"]], [b["date_ajout"] for b in avant])
        nouvelle_app = creer_app(self.config)
        self.assertEqual(database.consulter_cave(nouvelle_app.config["DATABASE"], 1)["stats"]["valeur_centimes"], 3249)
        self.assertEqual(self.envoyer("/bouteilles/1/prix", {"prix_achat": "0"}).status_code, 200)
        self.assertEqual(database.consulter_cave(self.chemin, 1)["stats"]["valeur_centimes"], 1250)

    def test_modifier_prix_refuse_valeurs_invalides(self):
        self.preparer_cave()
        self.envoyer("/bouteilles", {"etagere_id": "1", "vin_id": "1", "quantite": "1", "prix_achat": "12.50"})
        for prix in ("-1", "NaN", "Infinity", "1.001", "abc", "", "1000001"):
            with self.subTest(prix=prix):
                self.assertEqual(self.envoyer("/bouteilles/1/prix", {"prix_achat": prix}).status_code, 400)
                self.assertEqual(database.consulter_cave(self.chemin, 1)["stats"]["valeur_centimes"], 1250)

    def test_modifier_prix_verifie_acces_et_csrf(self):
        self.preparer_cave()
        self.envoyer("/bouteilles", {"etagere_id": "1", "vin_id": "1", "quantite": "1", "prix_achat": "12.50"})
        self.assertEqual(self.client.post("/bouteilles/1/prix", data={"prix_achat": "20"}).status_code, 400)
        self.assertEqual(self.client.get("/bouteilles/1/prix").status_code, 405)
        autre = self.app.test_client()
        self.inscrire("Bastien", autre)
        for bouteille_id in (1, 999):
            self.assertEqual(self.envoyer(f"/bouteilles/{bouteille_id}/prix", {"prix_achat": "20"}, autre).status_code, 404)
        visiteur = self.app.test_client()
        self.assertEqual(visiteur.post("/bouteilles/1/prix", data={"prix_achat": "20"}).status_code, 302)
        self.assertEqual(database.consulter_cave(self.chemin, 1)["stats"]["valeur_centimes"], 1250)

    def test_formulaire_sans_jeton_refuse(self):
        reponse = self.client.post("/etageres", data={"nom": "Test", "capacite": "3"})
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(len(database.consulter_cave(self.chemin, 1)["etageres"]), 0)

    def test_html_utilisateur_echappe(self):
        reponse = self.envoyer("/etageres", {"nom": "<script>alert(1)</script>", "capacite": "3"})
        html = reponse.get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
