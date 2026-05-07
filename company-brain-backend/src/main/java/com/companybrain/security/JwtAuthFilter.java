package com.companybrain.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.lang.NonNull;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.util.AntPathMatcher;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;
import java.util.UUID;

@Slf4j
@RequiredArgsConstructor
public class JwtAuthFilter extends OncePerRequestFilter {

    private static final String BEARER_PREFIX = "Bearer ";

    private static final List<String> PASS_THROUGH_PATTERNS = List.of(
            "/actuator/**",
            "/v1/ingest"
    );

    private final JwtUtil jwtUtil;
    private final WorkspaceContext workspaceContext;

    private final AntPathMatcher pathMatcher = new AntPathMatcher();

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getServletPath();
        return PASS_THROUGH_PATTERNS.stream().anyMatch(pattern -> pathMatcher.match(pattern, path));
    }

    @Override
    protected void doFilterInternal(@NonNull HttpServletRequest request,
                                    @NonNull HttpServletResponse response,
                                    @NonNull FilterChain filterChain)
            throws ServletException, IOException {

        String token = extractToken(request);

        try {
            if (jwtUtil.isTokenValid(token)) {
                UUID workspaceId = jwtUtil.extractWorkspaceId(token);
                workspaceContext.setWorkspaceId(workspaceId);

                // Set a minimal authentication so Spring Security considers the request authenticated
                UsernamePasswordAuthenticationToken auth =
                        new UsernamePasswordAuthenticationToken(workspaceId.toString(), null, List.of());
                SecurityContextHolder.getContext().setAuthentication(auth);

                log.debug("Authenticated workspace: {}", workspaceId);
            } else {
                log.debug("No valid JWT found for request: {}", request.getServletPath());
            }
        } catch (Exception e) {
            log.warn("JWT processing failed: {}", e.getMessage());
            // Don't set authentication — Spring Security will reject in the authorization phase
        }

        filterChain.doFilter(request, response);
    }

    private String extractToken(HttpServletRequest request) {
        String header = request.getHeader("Authorization");
        if (StringUtils.hasText(header) && header.startsWith(BEARER_PREFIX)) {
            return header.substring(BEARER_PREFIX.length());
        }
        return null;
    }
}
