import { DurableObject } from 'cloudflare:workers';

export class GroupRelay extends DurableObject {
  async fetch(): Promise<Response> {
    return new Response('not implemented\n', { status: 501 });
  }
}
