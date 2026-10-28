#!/usr/bin/env node
/**
 * arsenal-builder 付费门禁（AI 按量付费 / A2M 402 协议）客户端。
 *
 * 三个子命令只是本脚本的编排步骤，不是 Pay Skill 的固定命名：
 *   probe    —— 请求业务资源；未付款时预期 HTTP 402 + Payment-Needed 头，
 *               保存账单与原始请求供付款后复用。
 *   pay      —— 把账单与原始请求交给支付宝官方买家 CLI（alipay-bot），
 *               展示用途、金额和支付入口，等待用户扫码授权。
 *   complete —— 付款后按订单号查询原订单，继续原始资源请求；
 *               服务端验付后返回资源并完成履约确认。
 *
 * 本脚本不接触任何支付密钥，不自行实现签名或付款。
 * 官方买家依赖：npx -y @alipay/agent-payment@latest install
 */

import { execFile } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

const DEFAULT_RESOURCE_URL = process.env.ARSENAL_PAY_RESOURCE_URL || 'http://127.0.0.1:8000/arsenal/run';
const DEFAULT_METHOD = process.env.ARSENAL_PAY_METHOD || 'GET';
const DEFAULT_PRICE = process.env.ARSENAL_PAY_PRICE || '0.01';
const SESSION_KEY = process.env.ARSENAL_PAY_SESSION_KEY || 'default';

function parseArgs(argv) {
  const result = { _: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith('--')) {
      result._.push(token);
      continue;
    }
    const key = token.slice(2);
    const next = argv[index + 1];
    if (next === undefined || next.startsWith('--')) {
      result[key] = true;
    } else {
      result[key] = next;
      index += 1;
    }
  }
  return result;
}

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true, mode: 0o700 });
}

async function writeSecretFile(filePath, content) {
  await ensureDir(path.dirname(filePath));
  await fs.writeFile(filePath, content, { encoding: 'utf8', mode: 0o600 });
}

async function readState(stateDir) {
  const raw = await fs.readFile(path.join(stateDir, 'request.json'), 'utf8');
  return JSON.parse(raw);
}

async function writeState(stateDir, state) {
  await ensureDir(stateDir);
  await fs.writeFile(path.join(stateDir, 'request.json'), JSON.stringify(state, null, 2), {
    encoding: 'utf8',
    mode: 0o600,
  });
}

function buildCurlArgs(state, headerFile, bodyFile) {
  const args = ['-s', '-D', headerFile, '--connect-timeout', '10', '-o', bodyFile, '-w', 'HTTP_STATUS:%{http_code}'];
  if (state.method === 'POST') {
    args.push('-X', 'POST');
    if (state.bodyFile) {
      args.push('--data-binary', `@${state.bodyFile}`);
      args.push('-H', 'Content-Type: application/json; charset=utf-8');
    }
  }
  args.push(state.resourceUrl);
  return args;
}

function extractPaymentNeeded(headers) {
  const match = headers.match(/payment-needed:\s*([^\r\n]+)/i);
  return match ? match[1].trim() : '';
}

async function probe(args) {
  const stateDir = args['state-dir'] || path.join('.pay_cache', SESSION_KEY);
  const state = {
    resourceUrl: args['resource-url'] || DEFAULT_RESOURCE_URL,
    method: (args['method'] || DEFAULT_METHOD).toUpperCase(),
    bodyFile: args['body-file'] || '',
    sessionId: args['session-id'] || SESSION_KEY,
    intent: args['intent'] || '执行 Arsenal 沙箱测试门禁',
    price: args['price'] || DEFAULT_PRICE,
  };
  await writeState(stateDir, state);

  const headerFile = path.join(stateDir, 'headers.txt');
  const bodyFile = path.join(stateDir, 'body.bin');
  const { stdout } = await execFileAsync('curl', buildCurlArgs(state, headerFile, bodyFile), {
    timeout: 30000,
  });

  const status = Number(/HTTP_STATUS:(\d+)/.exec(stdout)?.[1] ?? 0);
  const headers = await fs.readFile(headerFile, 'utf8');

  if (status === 200) {
    const body = await fs.readFile(bodyFile, 'utf8');
    process.stdout.write(`已取得资源（HTTP 200），无需重新支付：\n${body}\n`);
    return 0;
  }

  if (status !== 402) {
    throw new Error(`资源请求返回 HTTP ${status}`);
  }

  const paymentNeeded = extractPaymentNeeded(headers);
  if (!paymentNeeded) {
    throw new Error('HTTP 402 缺少 Payment-Needed 响应头');
  }
  await writeSecretFile(path.join(stateDir, 'payment-needed.txt'), paymentNeeded);
  process.stdout.write(
    `已取得 402 账单，保存在 ${path.join(stateDir, 'payment-needed.txt')}\n` +
      `下一步：node scripts/arsenal-paid.mjs pay --state-dir "${stateDir}"\n`,
  );
  return 0;
}

async function pay(args) {
  const stateDir = args['state-dir'] || path.join('.pay_cache', SESSION_KEY);
  const state = await readState(stateDir);
  const sessionId = args['session-id'] || state.sessionId || SESSION_KEY;

  const cmdArgs = [
    '402-buyer-pay',
    '--session-id',
    sessionId,
    '--file',
    path.join(stateDir, 'payment-needed.txt'),
    '--resource-url',
    state.resourceUrl,
    '--method',
    state.method,
    '--intent-summary',
    `原始请求：${state.intent}`,
  ];

  await runAlipayBot(cmdArgs);
  return 0;
}

async function complete(args) {
  const stateDir = args['state-dir'] || path.join('.pay_cache', SESSION_KEY);
  const state = await readState(stateDir);
  const outShakeNo = args['out-shake-no'] || '';
  if (!outShakeNo) {
    throw new Error('缺少 --out-shake-no，请先在 pay 步骤取得订单号');
  }

  const result = await runAlipayBotCaptured([
    '402-query-payment-status',
    '--out-shake-no',
    outShakeNo,
    '--resource-url',
    state.resourceUrl,
    '--method',
    state.method,
  ]);
  process.stdout.write(result.stdout ?? '');
  process.stderr.write(result.stderr ?? '');
  return result.exitCode;
}

async function runAlipayBot(cmdArgs) {
  const { stdout } = await execFileAsync('alipay-bot', cmdArgs, { timeout: 120000 });
  process.stdout.write(stdout ?? '');
}

async function runAlipayBotCaptured(cmdArgs) {
  return execFileAsync('alipay-bot', cmdArgs, { timeout: 120000 });
}

// ==================== 模拟响应（仅本地自测，不参与生产路径）====================

const MOCKS = {
  '402': { status: 402, headers: { 'Payment-Needed': 'MOCK_PAYMENT_NEEDED' }, body: { code: 'Payment-Needed' } },
  processing: { status: 402, headers: { 'Payment-Needed': 'MOCK_PAYMENT_NEEDED' }, body: { code: 'PAYMENT_PROCESSING' } },
  success: { status: 200, headers: {}, body: { resource_id: '/arsenal/run', content: 'MOCK_OK' } },
  failed: { status: 402, headers: {}, body: { code: 'VERIFY_FAILED' } },
  retry: { status: 200, headers: {}, body: { resource_id: '/arsenal/run', content: 'MOCK_OK', already_fulfilled: true } },
};

async function mock(args) {
  const scenario = args.mock || args._?.[1] || '402';
  const payload = MOCKS[scenario];
  if (!payload) {
    throw new Error(`未知模拟场景：${scenario}（可选：${Object.keys(MOCKS).join(' / ')}）`);
  }
  process.stdout.write(`[mock:${scenario}] HTTP ${payload.status}\n`);
  if (payload.headers['Payment-Needed']) {
    process.stdout.write(`[mock:${scenario}] Payment-Needed: ${payload.headers['Payment-Needed']}\n`);
  }
  process.stdout.write(`${JSON.stringify(payload.body, null, 2)}\n`);
  return 0;
}

// ==================== 入口 ====================

const COMMANDS = { probe, pay, complete, mock };

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = args._[0];
  const handler = COMMANDS[command];
  if (!handler) {
    process.stderr.write('用法：arsenal-paid.mjs <probe|pay|complete|mock> [选项]\n');
    return 2;
  }
  return handler(args);
}

main().then(
  (code) => process.exit(code ?? 0),
  (error) => {
    process.stderr.write(`${error?.message || String(error)}\n`);
    process.exit(1);
  },
);
