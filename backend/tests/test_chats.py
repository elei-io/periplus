from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from control.chats.models import Chat, ChatItem
from control.chats.schemas import (
    AssistantTurnContent,
    ChatQueryArtifact,
    MAX_QUERY_ARTIFACT_CHARACTERS,
    MAX_QUERY_CELL_CHARACTERS,
    UserActionContent,
    UserMessageContent,
)
from control.chats.service import (
    MAX_CONTEXT_ITEMS,
    append_item,
    build_agent_context,
    create_chat,
    get_chat,
    list_chats,
)


class ChatServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Chat.__table__.create(self.engine)
        ChatItem.__table__.create(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_chat_persists_typed_turns_and_builds_context(self) -> None:
        chat = create_chat(self.session)
        append_item(
            self.session,
            chat.id,
            UserMessageContent(text="Compare Japanese marketplace prices."),
        )
        assistant = append_item(
            self.session,
            chat.id,
            AssistantTurnContent(
                summary="We should validate two marketplaces first.",
                queries=[
                    ChatQueryArtifact(
                        call_id="query-1",
                        sql="SELECT 1 AS value",
                        columns=["value"],
                        column_types=["INTEGER"],
                        rows=[[1]],
                    )
                ],
            ),
        )
        append_item(
            self.session,
            chat.id,
            UserActionContent(
                action="graph_run_started",
                related_item_id=assistant.id,
                graph_id=chat.id,
                graph_run_id=chat.id,
                label="Started a validation graph run.",
            ),
        )
        self.session.commit()

        record = get_chat(self.session, chat.id)
        self.assertEqual(record.title, "Compare Japanese marketplace prices.")
        self.assertEqual(len(record.items), 3)
        context = build_agent_context(self.session, chat.id)
        self.assertIn("SELECT 1 AS value", context)
        self.assertIn("Started a validation graph run", context)
        self.assertEqual(list_chats(self.session).items[0].item_count, 3)

    def test_context_is_bounded_to_recent_items(self) -> None:
        chat = create_chat(self.session)
        for index in range(MAX_CONTEXT_ITEMS + 5):
            append_item(
                self.session,
                chat.id,
                UserMessageContent(text=f"message-{index}"),
            )
        self.session.commit()

        context = build_agent_context(self.session, chat.id)
        self.assertNotIn("message-0\n", context)
        self.assertIn(f"message-{MAX_CONTEXT_ITEMS + 4}", context)

    def test_query_artifacts_bound_large_cell_values(self) -> None:
        artifact = ChatQueryArtifact(
            call_id="query-large",
            sql="SELECT passage FROM views.passages",
            columns=["passage"],
            rows=[["x" * (MAX_QUERY_CELL_CHARACTERS + 100)] for _ in range(200)],
        )

        self.assertLessEqual(len(artifact.rows[0][0]), MAX_QUERY_CELL_CHARACTERS)
        self.assertLessEqual(
            len(str(artifact.model_dump(mode="json")["rows"])),
            MAX_QUERY_ARTIFACT_CHARACTERS + 5_000,
        )


if __name__ == "__main__":
    unittest.main()
