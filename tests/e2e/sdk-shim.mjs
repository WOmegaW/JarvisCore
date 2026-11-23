// Минимальная замена 'sdk' Telegram Serverless для тестов.
export const fetch = (...a) => globalThis.fetch(...a);
export class EndpointError extends Error {
  constructor(description, parameters) { super(description); this.description = description; this.parameters = parameters; }
}
