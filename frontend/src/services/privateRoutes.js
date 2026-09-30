const PRIVATE_ROUTE_PATTERN = /^\/(admin|deep-search|login|signup|verify-email|forgot-password|reset-password|request-movie|support|contributions|paynow)(\/|$)/;

export const isPrivateRoute = (pathname = "") => PRIVATE_ROUTE_PATTERN.test(pathname);
