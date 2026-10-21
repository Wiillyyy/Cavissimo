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
