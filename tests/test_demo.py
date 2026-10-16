from drone_assistant.demo import run_demo


async def test_complete_offline_flow(capsys):
    result = await run_demo()
    assert result["passed"] and result["alerts"] == 3
    assert "DEMO PASSED" in capsys.readouterr().out


async def test_explicit_telegram_demo_labels_all_messages(capsys):
    class FakeTelegram:
        def __init__(self):
            self.messages = []

        async def send(self, target, text):
            self.messages.append((target, text))

    transport = FakeTelegram()
    await run_demo(transport, "123")
    assert len(transport.messages) == 3
    assert all(
        target == "123" and text.startswith("DEMO — SYNTHETIC INVENTORY CHANGE")
        for target, text in transport.messages
    )
