#!/usr/bin/env node
/**
 * Voting workflow smoke test
 * Tests that clicking advances the match index correctly.
 * Usage: node test_voting.js
 */

const { chromium } = require('playwright');

const SERVER = 'http://localhost:8000';
const CLICK_DELAY_MS = 400;
const TEST_COUNT = 20;

async function run() {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-setuid-sandbox'] });
  const context = await browser.newContext();
  const page = await context.newPage();

  const apiCalls = [];
  page.on('response', r => {
    if (r.url().includes('/api/match/')) apiCalls.push(r.url().split('/').pop());
  });

  console.log(`Navigating to ${SERVER}...`);
  await page.goto(SERVER);
  await page.waitForTimeout(2000);

  const startCmi = await page.evaluate(() => state.currentMatchIndex);
  const totalMatches = await page.evaluate(() => state.totalMatches);
  console.log(`Start: cmi=${startCmi}, totalMatches=${totalMatches}`);

  for (let i = 0; i < TEST_COUNT; i++) {
    await page.click('#choice-a');
    await page.waitForTimeout(CLICK_DELAY_MS);
    const cmi = await page.evaluate(() => state.currentMatchIndex);
    process.stdout.write(`${i + 1}/${TEST_COUNT} cmi=${cmi}\r`);
  }

  await page.waitForTimeout(1500);

  const endCmi = await page.evaluate(() => state.currentMatchIndex);
  const advanced = endCmi - startCmi;

  console.log(`\nResult: cmi ${startCmi} → ${endCmi} (+${advanced}/${TEST_COUNT})`);
  console.log(`API calls: ${apiCalls.length}/${TEST_COUNT}`);

  const passed = advanced === TEST_COUNT && apiCalls.length === TEST_COUNT;
  console.log(passed ? 'PASS' : 'FAIL');

  await browser.close();
  process.exit(passed ? 0 : 1);
}

run().catch(e => {
  console.error('Error:', e.message);
  process.exit(1);
});