from concurrent.futures import ThreadPoolExecutor
import os
import threading
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from control.crawl_policies.models import CrawlPolicy, CrawlProfile
from control.crawl_policies.schemas import DEFAULT_HTTP_USER_AGENT
from control.crawl_policies.service import apply_policy_trial_candidate
from db import Base
import db.models  # noqa: F401 - register the complete control-plane schema


@unittest.skipUnless(
    os.getenv("ATLAS_TEST_DATABASE_URL"),
    "Postgres crawl-policy integration is opt-in",
)
class PostgresCrawlPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.admin_dsn = os.environ["ATLAS_TEST_DATABASE_URL"]
        self.database_name = f"atlas_policy_test_{uuid4().hex}"
        with psycopg.connect(self.admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(self.database_name)
                )
            )
        url = make_url(self.admin_dsn).set(
            drivername="postgresql+psycopg",
            database=self.database_name,
        )
        self.engine = create_engine(url)
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add_all(
                [
                    CrawlProfile(
                        slug="direct",
                        name="Direct",
                        transport="http",
                        config={"user_agent": DEFAULT_HTTP_USER_AGENT},
                        cost_rank=10,
                        trial_eligible=True,
                    ),
                    CrawlProfile(
                        slug="rendered",
                        name="Rendered",
                        transport="browser",
                        config={"mode": "static"},
                        cost_rank=20,
                        trial_eligible=True,
                    ),
                ]
            )
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()
        with psycopg.connect(self.admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.database_name)
                )
            )

    def test_existing_policy_updates_without_locking_outer_join(self) -> None:
        with Session(self.engine) as session:
            created = apply_policy_trial_candidate(
                session,
                scheme="https",
                host="example.com",
                port=443,
                profile_slug="direct",
            )
            policy_id = created.id
            session.commit()

        with Session(self.engine) as session:
            updated = apply_policy_trial_candidate(
                session,
                scheme="https",
                host="example.com",
                port=443,
                profile_slug="rendered",
            )
            session.commit()
            self.assertEqual(updated.id, policy_id)
            self.assertEqual(updated.profile.slug, "rendered")

    def test_concurrent_first_application_creates_one_policy(self) -> None:
        barrier = threading.Barrier(2)

        def apply(profile_slug: str):
            with Session(self.engine) as session:
                barrier.wait(timeout=5)
                policy = apply_policy_trial_candidate(
                    session,
                    scheme="https",
                    host="example.com",
                    port=443,
                    profile_slug=profile_slug,
                )
                session.commit()
                return policy.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            policy_ids = list(executor.map(apply, ("direct", "rendered")))

        self.assertEqual(policy_ids[0], policy_ids[1])
        with Session(self.engine) as session:
            policies = session.scalars(
                select(CrawlPolicy).where(
                    CrawlPolicy.scheme == "https",
                    CrawlPolicy.host == "example.com",
                    CrawlPolicy.path_prefix == "/",
                    CrawlPolicy.path_mode == "prefix",
                )
            ).all()
            self.assertEqual(len(policies), 1)


if __name__ == "__main__":
    unittest.main()
