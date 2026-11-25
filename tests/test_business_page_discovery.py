"""Meta Page discovery for business portfolio and user-managed Pages."""
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
import os
import meta_connect
import tenant_meta

class BusinessPageTests(unittest.TestCase):
    def test_login_requests_business_management(self):
        self.assertIn("business_management",meta_connect.PERMISSIONS.split(","))
        with patch.dict(os.environ,{"META_APP_ID":"fake","PUBLIC_ORIGIN":"https://test.example",
                                    "SESSION_SECRET":"test-secret"}):
            url=meta_connect.authorize_url("admin")
            scope=parse_qs(urlsplit(url).query)["scope"][0].split(",")
            self.assertIn("business_management",scope)
            self.assertIn("pages_show_list",scope)

    def test_pages_from_business_portfolio_have_usable_page_token(self):
        calls=[]
        def graph(path,params,token=None):
            calls.append((path,params.get("after")))
            if path=="me/accounts":
                return {"data":[{"id":"111","name":"Social Likes","access_token":"page111"}]}
            if path=="me/businesses":
                return {"data":[{"id":"901","name":"British Arena Business"}]}
            if path=="901/owned_pages":
                return {"data":[{"id":"222","name":"British Arena"},
                                {"id":"111","name":"Social Likes"}]}
            if path=="901/client_pages":
                return {"data":[]}
            if path=="222":
                return {"id":"222","name":"British Arena","access_token":"page222",
                        "instagram_business_account":{"id":"ig222","username":"britisharena"}}
            raise ValueError("Unknown path")
        with patch.object(meta_connect,"graph",side_effect=graph):
            result=meta_connect.list_pages("private-user-token")
        self.assertEqual([p["name"] for p in result],["British Arena","Social Likes"])
        arena=result[0]
        self.assertEqual(arena["access_token"],"page222")
        self.assertEqual(arena["instagram_id"],"ig222")
        self.assertEqual(len([p for p in result if p["id"]=="111"]),1)
        self.assertIn(("901/owned_pages",None),calls)

    def test_business_page_without_page_token_is_not_connectable(self):
        def graph(path,params,token=None):
            if path=="me/accounts":return {"data":[]}
            if path=="me/businesses":return {"data":[{"id":"901"}]}
            if path=="901/owned_pages":return {"data":[{"id":"222","name":"British Arena"}]}
            if path=="901/client_pages":return {"data":[]}
            if path=="222":return {"id":"222","name":"British Arena"}
            raise ValueError("Not found")
        with patch.object(meta_connect,"graph",side_effect=graph):
            self.assertEqual(meta_connect.list_pages("user-token"),[])

    def test_business_discovery_failure_preserves_regular_pages(self):
        def graph(path,params,token=None):
            if path=="me/accounts":
                return {"data":[{"id":"123","name":"Social Likes","access_token":"page123"}]}
            raise ValueError("Business permission not granted")
        with patch.object(meta_connect,"graph",side_effect=graph):
            result=meta_connect.list_pages("user-token")
        self.assertEqual([r["id"] for r in result],["123"])

    def test_pagination_uses_cursor_not_untrusted_next_url(self):
        calls=[]
        def graph(path,params,token=None):
            calls.append((path,params.get("after")))
            if path=="me/accounts":
                if "after" not in params:
                    return {"data":[{"id":"111","name":"First","access_token":"a"}],
                            "paging":{"next":"https://attacker.example/?access_token=TOKEN",
                                      "cursors":{"after":"cursor-2"}}}
                return {"data":[{"id":"222","name":"Second","access_token":"b"}]}
            if path=="me/businesses":return {"data":[]}
            raise ValueError("unexpected")
        with patch.object(meta_connect,"graph",side_effect=graph):
            result=meta_connect.list_pages("super-secret")
        self.assertEqual(len(result),2)
        self.assertIn(("me/accounts","cursor-2"),calls)
        self.assertNotIn(("https://attacker.example/?access_token=TOKEN",None),calls)

    def test_wrong_page_id_in_detail_is_not_accepted(self):
        def graph(path,params,token=None):
            if path=="me/accounts":return {"data":[]}
            if path=="me/businesses":return {"data":[{"id":"901"}]}
            if path=="901/owned_pages":return {"data":[{"id":"222","name":"Arena"}]}
            if path=="901/client_pages":return {"data":[]}
            if path=="222":return {"id":"999","name":"Other","access_token":"wrong"}
            raise ValueError("Unexpected")
        with patch.object(meta_connect,"graph",side_effect=graph):
            self.assertEqual(meta_connect.list_pages("user"),[])

if __name__=="__main__":
    unittest.main()
