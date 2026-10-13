// EDMRN crew relay (spec B1 S17): routing only; each group is one Durable Object (src/group.ts).
import { joinPage } from './join';

export { GroupRelay } from './group';

export interface Env {
  GROUPS: DurableObjectNamespace;
  SOURCE_URL: string;
  CREW_LEADERS: string;
}

export const GROUP_PATH =
  /^\/v1\/groups\/([0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/ws$/;

function greeting(env: Env): string {
  const source = env.SOURCE_URL ? env.SOURCE_URL : 'not yet published';
  return `EDMRN crew relay. Licence: AGPL-3.0. Source: ${source}\n`;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === 'GET' && url.pathname === '/') {
      return new Response(greeting(env), { headers: { 'content-type': 'text/plain; charset=utf-8' } });
    }
    if (request.method === 'GET' && url.pathname === '/join') {
      return joinPage();
    }
    const match = GROUP_PATH.exec(url.pathname);
    if (match && request.method === 'GET') {
      if (request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') {
        return new Response('expected a WebSocket upgrade\n', { status: 426 });
      }
      const stub = env.GROUPS.get(env.GROUPS.idFromName(match[1]));
      return stub.fetch(request);
    }
    return new Response('not found\n', { status: 404 });
  },
} satisfies ExportedHandler<Env>;
