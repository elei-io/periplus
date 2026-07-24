export class ConsoleError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConsoleError";
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
