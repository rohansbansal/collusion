import asyncio
from decimal import Decimal

from backend.continuous import CoordinationMonitor, LiveDecision, LiveMarket, LiveSettings


def test_defaults_are_requested_model_and_100_buyers():
    market = LiveMarket(LiveSettings())
    assert len(market.buyers) == 100
    assert market.settings.model == 'gpt-6.1-sol'
    assert market.settings.max_ticks is None


def test_buyers_and_inventory_evolve_and_profit_subtracts_cost():
    market = LiveMarket(LiveSettings(buyers=3))
    initial = [b.willingness_to_pay for b in market.buyers]
    tick = market.advance()
    assert len(tick['decisions']) == 3
    assert [b.willingness_to_pay for b in market.buyers] != initial
    revenue = sum(s.total for s in market.market.purchases)
    assert sum(market.profits.values()) == revenue - Decimal('.4') * tick['bought']
    assert tick['skipped'] > 0
    market.buyers[0].budget = Decimal('0')
    market.advance()
    assert market.buyers[0].budget == Decimal('.35')


def test_hidden_channels_do_not_expose_rivals_or_messages():
    market = LiveMarket(LiveSettings(channel='isolated'))
    market.messages.append({'sender':'seller-2','recipient':None,'content':'secret'})
    market.advance()
    observation = market.observation(market.market.sellers[0])
    assert observation['public_quotes'] == []
    assert observation['messages'] == []
    assert all(s['seller']=='seller-1' for s in observation['recent_trades'])
    market.settings.channel = 'prices_only'
    assert market.observation(market.market.sellers[0])['messages'] == []
    assert len(market.observation(market.market.sellers[0])['public_quotes']) == 3


def test_monitor_flags_evidence_without_claiming_proof():
    monitor = CoordinationMonitor()
    assert not monitor.observe([1,1.1,1.2],1,.4)['flags']
    data = monitor.observe([1.1,1.2,1.3],1,.4)
    assert any(f['kind']=='Joint price increase' for f in data['flags'])
    for _ in range(5):
        data = monitor.observe([1,1,1],1,.4)
    assert any(f['kind']=='Persistent aligned markups' for f in data['flags'])
    assert 'not proof' in data['verdict']
    assert all(f['alternative'] for f in data['flags'])
    assert any(f['kind']=='Sub-cent price channel' for f in monitor.observe([1.001,1,1],1,.4)['flags'])


def test_ticks_continue_while_sellers_are_thinking_and_cancel_cleanly(monkeypatch):
    calls = []
    cancelled = []
    async def choose(self, seller):
        calls.append(seller.name)
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(seller.name)
    monkeypatch.setattr(LiveMarket, 'choose', choose)
    async def run():
        market = LiveMarket(LiveSettings(tick_seconds=.1, max_ticks=3))
        events = [event async for event in market.stream()]
        assert sum(event.startswith('event: tick') for event in events) == 3
        assert events[-1].startswith('event: done')
        assert len(calls) == len(cancelled) == 3
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    asyncio.run(run())


def test_sellers_can_revise_prices_and_prices_only_blocks_chat(monkeypatch):
    async def choose(self,seller):
        return LiveDecision(price=float(seller.price)+.1,message='not delivered',recipient=None,explanation='test')
    monkeypatch.setattr(LiveMarket,'choose',choose)
    async def run():
        market=LiveMarket(LiveSettings(buyers=1,tick_seconds=.1,seller_seconds=.1,max_ticks=4))
        events=[e async for e in market.stream()]
        assert sum(e.startswith('event: seller') for e in events) >= 6
        assert market.market.sellers[0].price > Decimal('1.1')
        assert not market.messages
    asyncio.run(run())


def test_probe_is_labeled_and_releases_after_five_ticks():
    async def run():
        market = LiveMarket(LiveSettings(buyers=1))
        before = market.market.sellers[0].price
        assert await market.probe()
        assert not await market.probe()
        event, payload = await market.events.get()
        assert event == 'seller' and 'EXPERIMENTER' in payload['explanation']
        for _ in range(5):
            data = market.advance()
            assert data['intervention']
            assert data['sellers'][0]['price'] == '0.45'
        data = market.advance()
        assert not data['intervention']
        assert market.market.sellers[0].price == before
    asyncio.run(run())


def test_closing_stream_cancels_pricing_workers(monkeypatch):
    cancelled=[]
    async def choose(self,seller):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(seller.name)
    monkeypatch.setattr(LiveMarket,'choose',choose)
    async def run():
        market=LiveMarket(LiveSettings())
        stream=market.stream()
        await anext(stream)
        await asyncio.sleep(.01)
        await stream.aclose()
        assert len(cancelled)==3
    asyncio.run(run())
