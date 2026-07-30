/**
 * Jaro-Winkler similarity, returning a score from 0 to 1.
 *
 * Build Loop only needs this one metric from `natural`. Keeping it local avoids
 * importing that package's storage adapters during MCP startup.
 */
export declare function jaroWinklerDistance(a: string, b: string): number;
//# sourceMappingURL=string-similarity.d.ts.map