package com.companybrain.security;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Date;
import java.util.UUID;

@Component
@Slf4j
public class JwtUtil {

    /** Default workspace UUID used in dev mode when no token is provided. */
    public static final UUID DEV_WORKSPACE_ID = UUID.fromString("00000000-0000-0000-0000-000000000001");

    private final SecretKey signingKey;
    private final Environment environment;

    public JwtUtil(@Value("${app.jwt.secret}") String secret, Environment environment) {
        this.environment = environment;
        // jjwt 0.12.x requires at least 256-bit (32-byte) key for HMAC-SHA256
        byte[] keyBytes = secret.getBytes(StandardCharsets.UTF_8);
        if (keyBytes.length < 32) {
            byte[] padded = new byte[32];
            System.arraycopy(keyBytes, 0, padded, 0, keyBytes.length);
            keyBytes = padded;
        }
        this.signingKey = Keys.hmacShaKeyFor(keyBytes);
    }

    /**
     * Extract the workspace_id claim from the JWT.
     * Returns the dev workspace UUID when running with the "dev" profile and no token is provided.
     */
    public UUID extractWorkspaceId(String token) {
        if (isDevMode() && (token == null || token.isBlank())) {
            log.debug("Dev mode: using default workspace {}", DEV_WORKSPACE_ID);
            return DEV_WORKSPACE_ID;
        }
        Claims claims = parseClaims(token);
        String workspaceIdStr = claims.get("workspace_id", String.class);
        if (workspaceIdStr == null) {
            throw new JwtException("JWT missing workspace_id claim");
        }
        return UUID.fromString(workspaceIdStr);
    }

    /**
     * Validate the JWT signature and expiry.
     * In dev mode, a null/blank token is considered valid.
     */
    public boolean isTokenValid(String token) {
        if (isDevMode() && (token == null || token.isBlank())) {
            return true;
        }
        try {
            Claims claims = parseClaims(token);
            return !claims.getExpiration().before(new Date());
        } catch (Exception e) {
            log.debug("Token validation failed: {}", e.getMessage());
            return false;
        }
    }

    private Claims parseClaims(String token) {
        return Jwts.parser()
                .verifyWith(signingKey)
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }

    private boolean isDevMode() {
        return Arrays.asList(environment.getActiveProfiles()).contains("dev");
    }
}
