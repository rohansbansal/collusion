<script>
  let rounds = 5;
  let buyers = 3;
  let buyer_budget = '10.00';
  let result = null;
  let error = '';
  let running = false;
  let progress = '';
  let progressTimer;
  let conversation = [];
  let roundResults = [];

  function money(value) {
    return value == null ? '—' : `$${Number(value).toFixed(2)}`;
  }

  function roundNumbers() {
    return [...new Set([
      ...conversation.map((turn) => turn.round),
      ...roundResults.map((round) => round.round)
    ])].sort((a, b) => a - b);
  }

  async function runExperiment() {
    running = true;
    error = '';
    result = null;
    conversation = [];
    roundResults = [];
    const steps = [
      'Starting experiment...',
      'Asking seller agents for decisions...',
      'Delivering seller messages...',
      'Running buyer purchases...',
      'Collecting results...'
    ];
    let step = 0;
    progress = steps[step];
    progressTimer = setInterval(() => {
      step = (step + 1) % steps.length;
      progress = steps[step];
    }, 1800);
    try {
      const response = await fetch('/run_experiment/stream', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ rounds: Number(rounds), buyers: Number(buyers), buyer_budget })
      });
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.detail ?? 'Experiment failed');
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
        const blocks = buffer.split('\n\n');
        buffer = blocks.pop();
        for (const block of blocks) {
          const event = block.match(/^event: (.+)$/m)?.[1];
          const data = block.match(/^data: (.+)$/m)?.[1];
          if (!event || !data) continue;
          const payload = JSON.parse(data);
          if (event === 'turn') conversation = [...conversation, payload];
          if (event === 'round') roundResults = [...roundResults, payload];
          if (event === 'status') progress = payload.message;
          if (event === 'error') throw new Error(payload.message);
          if (event === 'done') result = payload;
        }
        if (done) break;
      }
    } catch (requestError) {
      error = requestError.message;
    } finally {
      clearInterval(progressTimer);
      progress = '';
      running = false;
    }
  }
</script>

<main>
  <h1>Banana market</h1>
  <p>Run a small seller communication experiment.</p>

  <section class="flow">
    <h2>How a run works</h2>
    <ol>
      <li><strong>Observe:</strong> sellers see their own state and public market information.</li>
      <li><strong>Talk:</strong> sellers exchange messages until each seller ends its conversation.</li>
      <li><strong>Quote:</strong> sellers choose prices and quantities at the same time.</li>
      <li><strong>Buy:</strong> each buyer purchases one banana per round.</li>
      <li><strong>Record:</strong> prices, trades, revenue, decisions, and messages are saved.</li>
    </ol>
  </section>

  <form on:submit|preventDefault={runExperiment}>
    <p class="hint">Sellers can post publicly or send private notes to one another.</p>
    <label>Rounds <input type="number" min="1" max="100" bind:value={rounds} /></label>
    <label>Buyers <input type="number" min="1" max="100" bind:value={buyers} /></label>
    <label>Buyer budget <input bind:value={buyer_budget} /></label>
    <button disabled={running} aria-busy={running}>
      {#if running}<span class="spinner" aria-hidden="true"></span>{/if}
      {running ? 'Running...' : 'Run experiment'}
    </button>
    {#if running}<span class="progress">{progress}</span>{/if}
  </form>

  {#if error}<p class="error">{error}</p>{/if}
  {#if conversation.length || result || roundResults.length}
    <section class="round-list">
      <div class="section-heading"><div><span class="eyebrow">THE EXPERIMENT</span><h2>Rounds</h2></div><span class="live-dot">● live</span></div>
      {#each roundNumbers() as roundNumber}
        {@const roundResult = (result ? result.round_results : roundResults).find((round) => round.round === roundNumber)}
        <details class="round-card" open={roundNumber === roundNumbers().at(-1)}>
          <summary>
            <span><strong>Round {roundNumber}</strong>{#if !roundResult}<em> in progress</em>{/if}</span>
            {#if roundResult}<span class="summary-total">{roundResult.total_bananas_sold} bananas sold</span>{/if}
          </summary>
          {#if roundResult}
            <div class="results-grid">
              {#each roundResult.seller_results as seller}
                <div class="seller-result"><strong>{seller.seller}</strong><span>{seller.bananas_sold} sold · {seller.buyers} buyers</span><span>{money(seller.average_price)} avg.</span><b>{money(seller.revenue)}</b></div>
              {/each}
            </div>
          {/if}
          <div class="round-conversation">
            <div class="board-heading"><h3>Seller scratchpad</h3><span>messages between sellers</span></div>
            <div class="message-board">
            {#each conversation.filter((turn) => turn.round === roundNumber && turn.message) as turn}
              <article class:quote={turn.action === 'quote_submitted'}>
                <div class="speaker"><span class="avatar">{turn.seller.slice(-1)}</span><strong>{turn.seller}</strong> <small>turn {turn.turn}</small></div>
                <p class="bubble">{turn.message}</p>
                <small class="to">{turn.recipient ? `private note to ${turn.recipient}` : 'public note to all sellers'}</small>
              </article>
            {/each}
            {#if !conversation.some((turn) => turn.round === roundNumber && turn.message)}<p class="empty">The scratchpad is empty. Sellers are thinking.</p>{/if}
            </div>
            <h3 class="ready-heading">Quote readiness</h3>
            {#each conversation.filter((turn) => turn.round === roundNumber && turn.action === 'quote_submitted') as turn}
              <div class="ready-row"><span class="avatar">{turn.seller.slice(-1)}</span><strong>{turn.seller}</strong><span class="finished">✓ Ready to quote at {money(turn.price)}</span></div>
            {/each}
          </div>
        </details>
      {/each}
    </section>
  {/if}
</main>

<style>
  main { max-width: 640px; margin: 3rem auto; padding: 0 1rem; font: 16px system-ui; }
  label { display: block; margin: 1rem 0; }
  input, select, button { margin-top: .35rem; padding: .45rem; font: inherit; }
  input, select { display: block; width: 100%; box-sizing: border-box; }
  button { cursor: pointer; }
  button:disabled { cursor: wait; opacity: .75; }
  .spinner { display: inline-block; width: .8em; height: .8em; margin-right: .45em; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: spin .7s linear infinite; vertical-align: -.1em; }
  .progress { margin-left: .75rem; color: #555; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .error { color: #b00020; }
  .flow { background: #f7f7f7; padding: .75rem 1rem; }
  .flow li { margin: .4rem 0; }
  .section-heading { display: flex; justify-content: space-between; align-items: end; border-bottom: 1px solid #ddd; margin-bottom: 1rem; }
  .section-heading h2 { margin: .2rem 0 .6rem; }
  .eyebrow { color: #777; font-size: .7rem; font-weight: 700; letter-spacing: .12em; }
  .live-dot { color: #198754; font-size: .85rem; }
  .avatar { display: inline-grid; place-items: center; width: 1.5rem; height: 1.5rem; margin-right: .4rem; border-radius: 50%; background: #5b7cfa; color: white; font-size: .8rem; }
  .to { display: block; font-style: italic; }
  .empty { color: #777; font-size: .9rem; }
  article { border-left: 4px solid #5b7cfa; margin: 1rem 0; padding: .7rem 1rem; background: #f5f7ff; }
  article.quote { border-left-color: #35a66f; background: #f1fbf5; }
  .speaker { font-weight: 700; }
  small { color: #666; font-weight: 400; }
  .bubble { margin: .5rem 0; font-size: 1.05rem; }
  .finished { color: #237749; font-weight: 600; }
  .round-card { border: 1px solid #e1e1e1; border-radius: .5rem; padding: .8rem; margin-bottom: .8rem; box-shadow: 0 2px 8px #0000000a; }
  .round-card summary { display: flex; justify-content: space-between; cursor: pointer; padding: .2rem; }
  .round-card summary em { color: #777; font-size: .85rem; font-style: normal; }
  .summary-total { color: #777; font-size: .85rem; }
  .results-grid { margin-top: .8rem; }
  .seller-result { display: grid; grid-template-columns: 1.1fr 1.5fr 1fr auto; gap: .3rem; padding: .5rem 0; border-top: 1px solid #eee; font-size: .82rem; }
  .seller-result span { color: #666; }
  .round-conversation { border-top: 1px solid #eee; margin-top: .8rem; padding-top: .5rem; }
  .board-heading { display: flex; align-items: baseline; gap: .6rem; }
  .round-conversation h3 { font-size: .9rem; margin: .5rem 0; }
  .board-heading span { color: #888; font-size: .75rem; }
  .message-board { border: 1px solid #e4e7ef; border-radius: .5rem; padding: .25rem .8rem; background: linear-gradient(#fbfcff, #f5f7ff); }
  .message-board article { margin: .7rem 0; border-left-color: #8298f5; }
  .ready-heading { margin-top: 1rem !important; }
  .ready-row { display: flex; align-items: center; gap: .5rem; padding: .35rem 0; font-size: .85rem; }
</style>