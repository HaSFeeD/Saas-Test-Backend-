import type { FastifyBaseLogger } from 'fastify';
import type { Config } from '../config.js';

export interface Mail {
  to: string;
  subject: string;
  text: string;
}

export interface Mailer {
  send(mail: Mail): Promise<void>;
}

export class ConsoleMailer implements Mailer {
  /** Kept in memory so dev tooling can read what was "sent". */
  outbox: Mail[] = [];
  constructor(private log: FastifyBaseLogger) {}
  async send(mail: Mail) {
    this.outbox.push(mail);
    this.log.info({ to: mail.to, subject: mail.subject, text: mail.text }, 'mail (console)');
  }
}

export class ResendMailer implements Mailer {
  constructor(private apiKey: string, private from: string) {}
  async send(mail: Mail) {
    const res = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { Authorization: `Bearer ${this.apiKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: this.from, to: mail.to, subject: mail.subject, text: mail.text }),
    });
    if (!res.ok) throw new Error(`Resend error ${res.status}: ${await res.text()}`);
  }
}

export function createMailer(cfg: Config, log: FastifyBaseLogger): Mailer {
  return cfg.RESEND_API_KEY ? new ResendMailer(cfg.RESEND_API_KEY, cfg.MAIL_FROM) : new ConsoleMailer(log);
}
