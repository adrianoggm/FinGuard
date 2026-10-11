import { expect, test } from "@playwright/test";

const configured = Boolean(
  process.env.FINGUARD_E2E_TOKEN && process.env.FINGUARD_E2E_ORCHESTRATOR_URL,
);

function streamedAnswer(body: string): Record<string, any> {
  const answers = body
    .split("\n\n")
    .flatMap((frame) => frame.split("\n"))
    .filter((line) => line.startsWith("data:"))
    .map((line) => JSON.parse(line.slice(5).trim()) as Record<string, any>)
    .filter((payload) => payload.type === "answer");
  const answer = answers.at(-1);
  if (!answer) throw new Error("The question stream ended without an answer frame");
  return answer;
}

test("one banking session keeps list, analysis, visualization, and payment cancellation coherent", async ({
  page,
  request,
}, testInfo) => {
  test.skip(!configured, "Set FINGUARD_E2E_ORCHESTRATOR_URL and FINGUARD_E2E_TOKEN");

  const orchestratorUrl = process.env.FINGUARD_E2E_ORCHESTRATOR_URL!.replace(/\/$/, "");
  const [readyResponse, versionResponse] = await Promise.all([
    request.get(`${orchestratorUrl}/ready`),
    request.get(`${orchestratorUrl}/version`),
  ]);
  const [ready, version] = await Promise.all([
    readyResponse.json() as Promise<Record<string, any>>,
    versionResponse.json() as Promise<Record<string, any>>,
  ]);

  const requests: Array<{ question: string; conversation: Record<string, any> | null }> = [];
  const financialReads: Array<string | undefined> = [];

  async function recordTurn(
    question: string,
    answer: Record<string, any>,
    turn: number,
  ): Promise<void> {
    await testInfo.attach(`banking-session-turn-${String(turn).padStart(2, "0")}.json`, {
      body: Buffer.from(
        JSON.stringify(
          {
            turn,
            question,
            outcome: answer.outcome,
            conversation_version: answer.conversation?.version ?? null,
            movement_count: Array.isArray(answer.movements) ? answer.movements.length : null,
            group_count: Array.isArray(answer.groups) ? answer.groups.length : null,
            has_visualization: answer.visualization?.kind === "chart",
            workflow: answer.conversation?.workflow ?? null,
            financial_reads: financialReads.at(-1) ?? null,
          },
          null,
          2,
        ),
      ),
      contentType: "application/json",
    });
  }
  page.on("request", (request) => {
    if (request.url().endsWith("/v1/questions/stream") && request.method() === "POST") {
      requests.push(request.postDataJSON() as (typeof requests)[number]);
    }
  });

  await page.goto("/");
  const runtimeIdentity = {
    webSourceSha256: await page.locator("html").getAttribute("data-finguard-web-source"),
    orchestrator: {
      readyStatus: readyResponse.status(),
      ready,
      versionStatus: versionResponse.status(),
      version,
    },
  };
  await testInfo.attach("runtime-identity.json", {
    body: Buffer.from(JSON.stringify(runtimeIdentity, null, 2)),
    contentType: "application/json",
  });

  expect(readyResponse.status()).toBe(200);
  expect(versionResponse.status()).toBe(200);
  expect(ready.financial_path).toMatchObject({
    engine: "model",
    graph: "on",
    active_runtime: "langgraph",
    authoritative: true,
  });
  expect(ready.checks.persistence).toBe(true);
  expect(runtimeIdentity.webSourceSha256).toMatch(/^[a-f0-9]{64}$/);
  expect(version.source_sha256).toMatch(/^[a-f0-9]{64}$/);

  await page.getByRole("button", { name: "Pregúntale a FinGuard" }).click();
  const input = page.getByRole("textbox", { name: "Mensaje" });

  async function ask(question: string): Promise<Record<string, any>> {
    await input.fill(question);
    const response = page.waitForResponse(
      (item) =>
        item.url().endsWith("/v1/questions/stream") && item.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Enviar" }).click();
    const completed = await response;
    financialReads.push(completed.headers()["x-finguard-financial-reads"]);
    const answer = streamedAnswer(await completed.text());
    await recordTurn(question, answer, financialReads.length);
    return answer;
  }

  async function recordVisualDecision(
    name: string,
    answer: Record<string, any>,
  ): Promise<void> {
    const context = answer.conversation?.visual_contexts?.at(-1);
    expect(context?.chart_id).toBe(answer.visualization?.chart_id);
    expect(["llm", "deterministic"]).toContain(context?.source);
    await testInfo.attach(`${name}-planner.json`, {
      body: Buffer.from(
        JSON.stringify(
          {
            chart_id: context.chart_id,
            source: context.source,
            fallback_reason: context.fallback_reason,
            spec: answer.visualization.spec,
          },
          null,
          2,
        ),
      ),
      contentType: "application/json",
    });
  }

  const balances = await ask("¿Cómo están mis cuentas?");
  expect(balances.outcome).toBe("BALANCE");
  expect(balances.conversation.version).toBe(1);

  const current = await ask("Enséñame los últimos 20 gastos de Cuenta corriente.");
  expect(current.outcome).toBe("PLANNED");
  expect(current.movements).toHaveLength(20);
  expect(
    current.movements.every((movement: Record<string, any>) => movement.transaction_id),
  ).toBe(true);
  await expect(page.locator(".bubble.theirs").last().locator("ul.evidence li")).toHaveCount(20);

  const more = await ask("Dame otros 10.");
  expect(more.outcome).toBe("PLANNED");
  expect(more.movements).toHaveLength(10);
  const firstPageIds = new Set(
    current.movements.map((movement: Record<string, any>) => movement.transaction_id),
  );
  expect(
    more.movements.every(
      (movement: Record<string, any>) => !firstPageIds.has(movement.transaction_id),
    ),
  ).toBe(true);

  const movementChart = await ask("¿Me los enseñas en una gráfica?");
  expect(movementChart.visualization.kind).toBe("chart");
  await recordVisualDecision("movement-page-chart", movementChart);
  expect(financialReads.at(-1)).toBe("0");
  await expect(page.locator(".bubble.theirs").last().getByRole("figure")).toBeVisible();

  const hucha = await ask("¿Y los de la Hucha?");
  expect(hucha.outcome).toBe("PLANNED");
  expect(hucha.movements).toHaveLength(0);
  expect(hucha.text).toContain("Consultado en Hucha");
  expect(hucha.text).not.toContain("¿Qué quieres consultar?");

  const restaurants = await ask("¿Cuánto gasté en restaurantes el mes pasado?");
  expect(restaurants.outcome).toBe("PLANNED");
  expect(restaurants.plan_context.categories).toContain("RESTAURANTS");

  const comparison = await ask("Compáralo con agosto.");
  expect(comparison.outcome).toBe("PLANNED");
  expect(comparison.groups).toHaveLength(2);

  const chart = await ask("Ponme una gráfica.");
  expect(chart.visualization.kind).toBe("chart");
  await recordVisualDecision("restaurant-comparison-chart", chart);
  expect(financialReads.at(-1)).toBe("0");
  await expect(page.locator(".bubble.theirs").last().getByRole("figure")).toBeVisible();

  const capability = await ask("¿Puedes hacer Bizums?");
  expect(capability.text).toContain("preparar un Bizum");
  expect(capability.text).toContain("No se envía hasta que confirmes");
  expect(capability.conversation.workflow).toBeNull();

  const preview = await ask("Vale, quiero enviar 15 euros a Marta Ruiz Gomez.");
  expect(preview.text).toContain("15,00");
  expect(preview.text).toContain("Marta Ruiz Gomez");
  expect(preview.conversation.workflow).toBe("PAYMENT_CONFIRMATION");
  expect(preview.conversation.version).toBe(10);
  await expect(page.getByRole("button", { name: "Sí, enviar" })).toBeVisible();
  await expect(page.getByRole("button", { name: "No", exact: true })).toBeVisible();

  const cancelledResponse = page.waitForResponse(
    (item) =>
      item.url().endsWith("/v1/questions/stream") && item.request().method() === "POST",
  );
  await page.getByRole("button", { name: "No", exact: true }).click();
  const cancellation = await cancelledResponse;
  const cancelled = streamedAnswer(await cancellation.text());
  financialReads.push(cancellation.headers()["x-finguard-financial-reads"]);
  await recordTurn("No, mejor déjalo.", cancelled, financialReads.length);
  expect(cancelled.outcome).toBe("CANCELLED");
  expect(cancelled.conversation.workflow).toBeNull();
  expect(cancelled.conversation.version).toBe(11);
  await expect(page.getByRole("button", { name: "Sí, enviar" })).toHaveCount(0);

  expect(requests).toHaveLength(11);
  expect(requests.slice(1).every((request) =>
    request.conversation?.conversation_id === balances.conversation.conversation_id)).toBe(true);
  expect(requests.slice(1).map((request) => request.conversation?.version)).toEqual(
    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
  );
});
