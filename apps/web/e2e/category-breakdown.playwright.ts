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

test("category breakdown renders, restyles, and regroups its chart", async ({
  page,
}) => {
  test.skip(!configured, "Set FINGUARD_E2E_ORCHESTRATOR_URL and FINGUARD_E2E_TOKEN");

  const requests: Array<{ question: string; conversation: Record<string, unknown> | null }> = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/v1/questions/stream") && request.method() === "POST") {
      requests.push(request.postDataJSON() as (typeof requests)[number]);
    }
  });

  await page.goto("/");
  const openChat = page.getByRole("button", { name: "Hablar con FinGuard" }).last();
  await expect(openChat).toBeVisible();
  await openChat.click();

  const input = page.getByRole("textbox", { name: "Mensaje" });
  await input.fill(
    "Desglósame mis gastos por categorías los últimos 3 meses y enséñamelo en un gráfico",
  );
  const firstResponse = page.waitForResponse(
    (response) => response.url().endsWith("/v1/questions/stream") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Enviar" }).click();
  const first = streamedAnswer(await (await firstResponse).text());

  expect(first.outcome).toBe("PLANNED");
  expect(first.plan_context.result_kind).toBe("BREAKDOWN");
  expect(first.groups).toHaveLength(9);
  expect(first.visualization.kind).toBe("chart");
  expect(first.plan_context.query_id).toBeTruthy();
  expect(first.plan_context.artifact_id).toBeTruthy();
  expect(first.plan_context.view_id).toBe(first.visualization.chart_id);
  expect(first.visual_response.layout).toBe("financial_analysis");
  expect(first.visual_response.blocks.map((block: { type: string }) => block.type)).toEqual([
    "metric",
    "breakdown",
    "chart",
  ]);
  expect(first.visual_response.blocks.every((block: { artifact_id: string }) =>
    block.artifact_id === first.plan_context.artifact_id)).toBe(true);
  expect(first.conversation.version).toBe(1);

  const firstBubble = page.locator(".bubble.theirs").last();
  await expect(firstBubble.getByRole("img", { name: /Gastos por categor/i })).toBeVisible();
  await expect(firstBubble.getByRole("group", { name: "Resumen financiero" })).toBeVisible();
  await expect(firstBubble.locator(".financial-metric")).toHaveCount(1);
  await firstBubble.getByText("Ver los datos").click();
  await expect(firstBubble.locator(".groups li")).toHaveCount(9);

  await input.fill("Ahora en líneas");
  const secondResponse = page.waitForResponse(
    (response) => response.url().endsWith("/v1/questions/stream") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Enviar" }).click();
  const second = streamedAnswer(await (await secondResponse).text());

  expect(requests).toHaveLength(2);
  expect(requests[1]?.conversation?.conversation_id).toBe(first.conversation.conversation_id);
  expect(requests[1]?.conversation?.version).toBe(1);
  expect(second.conversation.version).toBe(2);
  expect(second.plan_context.query_id).toBe(first.plan_context.query_id);
  expect(second.plan_context.artifact_id).toBe(first.plan_context.artifact_id);
  expect(second.plan_context.view_id).not.toBe(first.plan_context.view_id);
  expect(second.visualization.chart_id).toBe(second.plan_context.view_id);
  expect(second.visual_response.blocks.map((block: { type: string }) => block.type)).toContain("chart");
  await expect(page.locator(".bubble.theirs").last()).toContainText("He actualizado el gráfico");
  await input.fill("Agrúpalo por mes");
  const thirdResponse = page.waitForResponse(
    (response) => response.url().endsWith("/v1/questions/stream") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Enviar" }).click();
  const third = streamedAnswer(await (await thirdResponse).text());

  expect(requests).toHaveLength(3);
  expect(requests[2]?.conversation?.conversation_id).toBe(first.conversation.conversation_id);
  expect(requests[2]?.conversation?.version).toBe(2);
  expect(third.outcome).toBe("PLANNED");
  expect(third.conversation.version).toBe(3);
  expect(third.plan_context.group_by).toBe("MONTH");
  expect(third.plan_context.result_kind).toBe("TIME_SERIES");
  expect(third.plan_context.query_id).not.toBe(second.plan_context.query_id);
  expect(third.plan_context.artifact_id).not.toBe(second.plan_context.artifact_id);
  expect(third.visualization.kind).toBe("chart");
  expect(third.visualization.data.map((row: { dimension: string }) => row.dimension)).toEqual([
    "2026-07",
    "2026-08",
    "2026-09",
    "2026-10",
  ]);
  expect(third.visual_response.blocks.map((block: { type: string }) => block.type)).toContain("chart");
  const thirdBubble = page.locator(".bubble.theirs").last();
  await expect(thirdBubble).toContainText("por mes");
  await expect(thirdBubble.getByRole("figure", { name: /Gastos por mes/i })).toBeVisible();
});
