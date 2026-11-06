"""Route normalization, matching, links and chains."""

import unittest

from helpers import fixture_maps
from pmdoc.actions.link import (
    RouteRef, build_links, infer_common_prefix, join_base, match_route, normalize_path,
)


class NormalizeTest(unittest.TestCase):
    def test_param_styles(self):
        self.assertEqual(normalize_path("articles/${slug}/favorite"), "/articles/{slug}/favorite")
        self.assertEqual(normalize_path("/users/:id"), "/users/{id}")
        self.assertEqual(normalize_path("/users/<int:id>/posts"), "/users/{id}/posts")
        self.assertEqual(normalize_path("/posts/[slug]"), "/posts/{slug}")
        self.assertEqual(normalize_path("a/${encodeURIComponent(a.slug)}"), "/a/{slug}")

    def test_query_fragment_case_and_slashes(self):
        self.assertEqual(normalize_path("articles/feed?${objectToQueryString(f)}"), "/articles/feed")
        self.assertEqual(normalize_path("//Articles//X/#top"), "/articles/x")
        self.assertEqual(normalize_path("https://h.test/api/tags?x=1"), "/api/tags")

    def test_prefix_stripping(self):
        self.assertEqual(normalize_path("/api/articles", ["/api"]), "/articles")
        self.assertEqual(normalize_path("/apiary", ["/api"]), "/apiary")
        self.assertEqual(normalize_path("/api", ["/api"]), "/")

    def test_join_base(self):
        self.assertEqual(join_base("articles", "https://shop.example.com/api/"), "/api/articles")
        self.assertEqual(join_base("/abs", "https://x/api/"), "/abs")
        self.assertEqual(join_base("articles", None), "articles")

    def test_infer_common_prefix(self):
        self.assertEqual(infer_common_prefix(["/api/a", "/api/b/{x}"]), "/api")
        self.assertIsNone(infer_common_prefix(["/api/a", "/v1/b"]))
        self.assertIsNone(infer_common_prefix(["/api/a"]))


class MatchTest(unittest.TestCase):
    routes = [RouteRef("slug", "GET", "/articles/{slug}", 0),
              RouteRef("feed", "GET", "/articles/feed", 1),
              RouteRef("del-comment", "DELETE", "/articles/{slug}/comments/{id}", 2)]

    def test_literal_beats_param(self):
        self.assertEqual(match_route("GET", "/articles/feed", self.routes).action_id, "feed")
        self.assertEqual(match_route("GET", "/articles/{x}", self.routes).action_id, "slug")

    def test_param_names_do_not_matter(self):
        hit = match_route("DELETE", "/articles/{s}/comments/{commentId}", self.routes)
        self.assertEqual(hit.action_id, "del-comment")

    def test_method_and_length_must_agree(self):
        self.assertIsNone(match_route("POST", "/articles/feed", self.routes))
        self.assertIsNone(match_route("GET", "/articles/feed/extra", self.routes))


class LinksTest(unittest.TestCase):
    def setUp(self):
        self.body = build_links(fixture_maps(), ["/api"])
        self.pairs = {(lk["from"], lk["to"]) for lk in self.body["links"]}

    def test_publish_links_to_create(self):
        self.assertIn(("frontend:ui:publish-article", "backend:api:create-article"), self.pairs)

    def test_feed_query_string_links_to_feed_not_slug(self):
        self.assertIn(("frontend:ui:open-your-feed", "backend:api:get-feed"), self.pairs)
        self.assertNotIn(("frontend:ui:open-your-feed", "backend:api:get-article"), self.pairs)

    def test_shared_handler_routes_both_link(self):
        self.assertIn(("frontend:ui:favorite-article", "backend:api:favorite-article"), self.pairs)
        self.assertIn(("frontend:ui:favorite-article", "backend:api:unfavorite-article"), self.pairs)

    def test_unlinked(self):
        self.assertIn("backend:api:get-tags", self.body["unlinked_routes"])
        self.assertEqual([u["from"] for u in self.body["unlinked_calls"]],
                         ["frontend:ui:report-article"])

    def test_chains(self):
        chains = {c["id"]: c for c in self.body["chains"]}
        pub = chains["chain:publish-article"]
        self.assertEqual(pub["actions"], ["frontend:ui:publish-article", "backend:api:create-article"])
        self.assertEqual(pub["repos"], ["frontend", "backend"])
        self.assertIn("chain:nightly-digest", chains)          # process gets its own chain
        self.assertIn("chain:get-tags", chains)                # orphan route too
        self.assertNotIn("chain:create-article", chains)       # covered by the UI chain

    def test_prefix_inferred_without_config(self):
        pairs = {(lk["from"], lk["to"]) for lk in build_links(fixture_maps(), [])["links"]}
        self.assertEqual(pairs, self.pairs)


if __name__ == "__main__":
    unittest.main()
