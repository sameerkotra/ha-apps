"""Stories about a person: CRUD, limits, validation, history/undo and
what happens when the person is trashed or purged."""
from base import ADMIN, BOB, ApiTestCase, sql
import _env  # noqa: F401

import unittest

from app.routers import stories as stories_router


class StoryCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.ann = self.person("Ann", "Smith")

    def add(self, pid=None, title="How she met Bob", body="It was raining.", user=None):
        return self.post(f"/api/people/{pid or self.ann}/stories", {"title": title, "body": body}, user=user)

    def add_ok(self, **kw):
        body = self.ok(self.add(**kw), 201)
        self.assertIn("batchId", body)
        return body["story"]

    def stories(self, pid=None):
        return self.ok(self.get(f"/api/people/{pid or self.ann}/stories"))

    def undo(self, bid):
        return self.ok(self.post(f"/api/history/{bid}/undo"))


class Crud(StoryCase):
    def test_add_list_edit_delete(self):
        s = self.add_ok(title="  How   she met Bob ", body="\n  It was raining.\n\nThe end.  \n")
        self.assertEqual((s["title"], s["body"]), ("How she met Bob", "It was raining.\n\nThe end."))
        self.assertEqual((s["personId"], s["createdBy"]), (self.ann, "Alice A"))
        self.assertEqual(s["createdAt"], s["updatedAt"])
        s2 = self.add_ok(title="Second", body="More", user=BOB)
        self.assertEqual(s2["createdBy"], "Bob B")
        self.assertEqual([x["id"] for x in self.stories()], [s["id"], s2["id"]])      # oldest first
        body = self.ok(self.patch(f"/api/stories/{s['id']}", {"body": "It was sunny."}))
        self.assertEqual((body["story"]["title"], body["story"]["body"]), ("How she met Bob", "It was sunny."))
        self.assertIn("batchId", body)
        body = self.ok(self.patch(f"/api/stories/{s['id']}", {"title": "Meeting Bob"}))
        self.assertEqual((body["story"]["title"], body["story"]["body"]), ("Meeting Bob", "It was sunny."))
        body = self.ok(self.delete(f"/api/stories/{s['id']}"))
        self.assertIn("batchId", body)
        self.assertEqual([x["id"] for x in self.stories()], [s2["id"]])
        self.assertEqual(self.delete(f"/api/stories/{s['id']}").status_code, 404)
        self.assertEqual(self.patch(f"/api/stories/{s['id']}", {"title": "x"}).status_code, 404)

    def test_stories_are_per_person(self):
        bob = self.person("Bob")
        self.add_ok()
        self.assertEqual(self.stories(bob), [])
        self.assertEqual(self.detail(bob)["storyCount"], 0)
        self.assertEqual(self.detail(self.ann)["storyCount"], 1)

    def test_plain_text_is_stored_as_is(self):
        s = self.add_ok(title="<b>bold</b>", body="<script>alert(1)</script>")
        self.assertEqual((s["title"], s["body"]), ("<b>bold</b>", "<script>alert(1)</script>"))

    def test_history_labels_and_person_history(self):
        s = self.add_ok()
        self.ok(self.patch(f"/api/stories/{s['id']}", {"title": "New"}))
        self.ok(self.delete(f"/api/stories/{s['id']}"))
        labels = [b["label"] for b in self.ok(self.get(f"/api/history?personId={self.ann}"))["items"]]
        self.assertEqual(labels[:3], ["Deleted a story about Ann Smith", "Edited a story about Ann Smith",
                                      "Added a story about Ann Smith"])
        bid = self.ok(self.get("/api/history"))["items"][0]["id"]
        detail = self.ok(self.get(f"/api/history/{bid}"))
        self.assertEqual(detail["changes"][0]["entity"], "story")


class Validation(StoryCase):
    def test_add_validation(self):
        for body in ({"title": "", "body": "x"}, {"title": "x", "body": ""}, {"title": "   ", "body": "x"},
                     {"title": "x", "body": " \n\t "}, {"title": "x" * 201, "body": "x"},
                     {"title": "x", "body": "x" * 50001}, {"title": "x"}, {"body": "x"},
                     {"title": "x", "body": "y", "personId": "z"}, {"title": None, "body": "x"}):
            with self.subTest(body=body):
                self.assertEqual(self.post(f"/api/people/{self.ann}/stories", body).status_code, 422)
        self.assertEqual(self.stories(), [])
        # the limits themselves are fine
        self.add_ok(title="x" * 200, body="y" * 50000)

    def test_edit_validation(self):
        s = self.add_ok()
        for body in ({"title": ""}, {"body": ""}, {"title": "  "}, {"body": "\n \n"}, {"title": "x" * 201},
                     {"body": "x" * 50001}, {"personId": self.ann}):
            with self.subTest(body=body):
                self.assertEqual(self.patch(f"/api/stories/{s['id']}", body).status_code, 422)
        self.assertEqual(self.stories()[0]["title"], "How she met Bob")

    def test_unknown_or_trashed_person(self):
        self.assertEqual(self.add(pid="nope").status_code, 404)
        self.assertEqual(self.get("/api/people/nope/stories").status_code, 404)
        s = self.add_ok()
        self.ok(self.delete(f"/api/people/{self.ann}"))
        self.assertEqual(self.add().status_code, 404)
        self.assertEqual(self.get(f"/api/people/{self.ann}/stories").status_code, 404)
        self.assertEqual(self.patch(f"/api/stories/{s['id']}", {"title": "x"}).status_code, 404)
        # restoring the person brings their stories back with them
        self.ok(self.post(f"/api/people/{self.ann}/restore", user=ADMIN))
        self.assertEqual([x["id"] for x in self.stories()], [s["id"]])

    def test_limit_per_person(self):
        stories_router.MAX_STORIES_PER_PERSON, old = 3, stories_router.MAX_STORIES_PER_PERSON
        self.addCleanup(setattr, stories_router, "MAX_STORIES_PER_PERSON", old)
        for i in range(3):
            self.add_ok(title=f"S{i}")
        r = self.add(title="One too many")
        self.assertEqual(r.status_code, 422)
        self.assertIn("at most 3", r.json()["detail"])
        self.add_ok(pid=self.person("Other"))           # the limit is per person

    def test_default_limit_is_100(self):
        self.assertEqual(stories_router.MAX_STORIES_PER_PERSON, 100)


class Undo(StoryCase):
    def test_undo_add_and_redo(self):
        s = self.add_ok()
        bid = sql("SELECT id FROM batches ORDER BY rowid DESC LIMIT 1")[0]["id"]
        u = self.undo(bid)
        self.assertEqual(self.stories(), [])
        self.undo(u["batchId"])
        self.assertEqual([x["id"] for x in self.stories()], [s["id"]])

    def test_undo_edit(self):
        s = self.add_ok()
        bid = self.ok(self.patch(f"/api/stories/{s['id']}", {"title": "Changed", "body": "Changed too"}))["batchId"]
        self.undo(bid)
        got = self.stories()[0]
        self.assertEqual((got["title"], got["body"], got["updatedAt"]), (s["title"], s["body"], s["updatedAt"]))

    def test_undo_delete_puts_it_back_exactly(self):
        s = self.add_ok()
        bid = self.ok(self.delete(f"/api/stories/{s['id']}"))["batchId"]
        self.undo(bid)
        self.assertEqual(self.stories(), [s])

    def test_undo_edit_conflicts_after_later_edit(self):
        s = self.add_ok()
        bid = self.ok(self.patch(f"/api/stories/{s['id']}", {"title": "One"}))["batchId"]
        self.ok(self.patch(f"/api/stories/{s['id']}", {"title": "Two"}))
        self.assertEqual(self.post(f"/api/history/{bid}/undo").status_code, 409)

    def test_undo_person_create_refused_while_they_have_stories(self):
        r = self.ok(self.post("/api/people", {"given_names": "Zed"}), 201)
        self.add_ok(pid=r["person"]["id"])
        self.assertEqual(self.post(f"/api/history/{r['batchId']}/undo").status_code, 409)

    def test_undo_story_delete_after_person_purged_conflicts(self):
        s = self.add_ok()
        bid = self.ok(self.delete(f"/api/stories/{s['id']}"))["batchId"]
        self.ok(self.delete(f"/api/people/{self.ann}"))
        self.ok(self.delete("/api/trash", user=ADMIN))
        self.assertEqual(self.post(f"/api/history/{bid}/undo").status_code, 409)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM stories")[0]["n"], 0)


class Purge(StoryCase):
    def test_person_purge_removes_their_stories(self):
        self.add_ok()
        self.add_ok(title="Another")
        keep = self.add_ok(pid=self.person("Bob"))
        self.ok(self.delete(f"/api/people/{self.ann}"))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM stories")[0]["n"], 3)       # trash keeps them
        purged = self.ok(self.delete("/api/trash", user=ADMIN))["purged"]
        self.assertEqual(purged["people"], 1)
        self.assertEqual([r["id"] for r in sql("SELECT id FROM stories")], [keep["id"]])


if __name__ == "__main__":
    unittest.main()
