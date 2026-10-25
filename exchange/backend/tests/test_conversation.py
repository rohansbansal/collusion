from decimal import Decimal

from backend.agents import SellerAgent, SellerDecision
from backend.experiment import Experiment


def test_sellers_read_and_reply_before_quoting_and_stream_every_action(monkeypatch):
    observations = []

    def decide(self, observation, model):
        observations.append((self.name, observation))
        if observation.conversation_stage == 'opening':
            # Even a model attempting to finish early must stay in the conversation.
            return [SellerDecision(action='public_message', content=f'{self.name} proposal'),
                    SellerDecision(action='quote_submitted', price=Decimal('2'))]
        if observation.conversation_stage == 'reply':
            assert any(m.sender != self.name for m in observation.public_messages)
            return [SellerDecision(action='private_message', recipient='seller-2' if self.name == 'seller-1' else 'seller-1', content='reply')]
        return [SellerDecision(action='public_message', content='final response'),
                SellerDecision(action='quote_submitted', price=Decimal('2'))]

    monkeypatch.setattr(SellerAgent, 'decide_with_langchain', decide)
    experiment = Experiment()
    streamed = []
    experiment.run(on_turn=streamed.append)

    assert streamed == experiment.seller_turns
    assert len(streamed) == 12
    assert all(t.turn >= 2 for t in streamed if t.action == 'quote_submitted')
    assert len([t for t in streamed if t.stage == 'reply']) == 3
    for name, observation in observations:
        assert all(m.recipient == name for m in observation.private_messages)
        if observation.conversation_stage != 'opening':
            assert len(observation.public_messages) >= 3
    assert all(t.peer_messages_read >= 2 for t in streamed if t.stage == 'reply')


def test_closed_sellers_are_announced_and_cannot_receive_messages(monkeypatch):
    observations = []

    def decide(self, observation, model):
        observations.append((self.name, observation))
        if observation.conversation_stage != 'negotiate':
            return [SellerDecision(action='public_message', content='proposal')]
        if self.name == 'seller-1' and observation.conversation_turn == 2:
            return [SellerDecision(action='quote_submitted', price=Decimal('1')),
                    SellerDecision(action='public_message', content='should never be sent')]
        if observation.conversation_turn == 2:
            assert 'seller-1' in observation.closed_sellers
            assert 'seller-1' not in observation.active_sellers
            return [SellerDecision(action='private_message', recipient='seller-1', content='blocked')]
        return [SellerDecision(action='quote_submitted', price=Decimal('1'))]

    monkeypatch.setattr(SellerAgent, 'decide_with_langchain', decide)
    experiment = Experiment()
    experiment.run()
    assert not any(m.recipient == 'seller-1' for m in experiment.bus.messages)
    assert not any(m.content == 'should never be sent' for m in experiment.bus.messages)
    assert len([t for t in experiment.seller_turns if t.action == 'message_blocked']) == 2
    assert len([o for name, o in observations if name == 'seller-1']) == 3


def test_every_buyer_decision_is_streamed_including_no_cash(monkeypatch):
    from backend.agents import BuyerAgent

    def decide(self, observation, model):
        if observation.conversation_stage != 'negotiate':
            return [SellerDecision(action='public_message', content='proposal')]
        return [SellerDecision(action='quote_submitted', price=Decimal('1'))]

    monkeypatch.setattr(SellerAgent, 'decide_with_langchain', decide)
    experiment = Experiment(buyers=[BuyerAgent(name='buyer', budget=Decimal('1'))], rounds=2)
    streamed = []
    purchases = experiment.run(on_buyer=streamed.append)
    assert streamed == experiment.buyer_decisions
    assert [d.action for d in streamed] == ['buy', 'skip']
    assert len(purchases) == 1
    assert len(experiment.round_results) == 2
