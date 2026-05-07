package com.companybrain.config;

import com.companybrain.security.WorkspaceContext;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.lang.NonNull;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import javax.sql.DataSource;
import java.util.UUID;

@Configuration
@Slf4j
public class DatabaseConfig {

    @Bean
    public JdbcTemplate jdbcTemplate(DataSource dataSource) {
        return new JdbcTemplate(dataSource);
    }

    // ----------------------------------------------------------------
    // Inner interceptor — sets Postgres session variable for RLS
    // ----------------------------------------------------------------

    @RequiredArgsConstructor
    @Slf4j
    public static class RlsInterceptor implements HandlerInterceptor {

        private final JdbcTemplate jdbcTemplate;
        private final WorkspaceContext workspaceContext;

        @Override
        public boolean preHandle(@NonNull HttpServletRequest request,
                                 @NonNull HttpServletResponse response,
                                 @NonNull Object handler) {
            UUID wid = workspaceContext.getWorkspaceId();
            if (wid != null) {
                try {
                    // SET persists for the connection within this request cycle.
                    // Using string concat is safe here — wid is a validated UUID (no injection risk).
                    jdbcTemplate.execute("SET app.workspace_id = '" + wid + "'");
                    log.debug("RLS: set app.workspace_id = {}", wid);
                } catch (Exception e) {
                    log.warn("Failed to set RLS session variable: {}", e.getMessage());
                }
            }
            return true;
        }
    }

    // ----------------------------------------------------------------
    // Register the interceptor with MVC
    // ----------------------------------------------------------------

    @Configuration
    @RequiredArgsConstructor
    public static class RlsWebMvcConfigurer implements WebMvcConfigurer {

        private final JdbcTemplate jdbcTemplate;
        private final WorkspaceContext workspaceContext;

        @Override
        public void addInterceptors(InterceptorRegistry registry) {
            registry.addInterceptor(new RlsInterceptor(jdbcTemplate, workspaceContext))
                    .addPathPatterns("/v1/**");
        }
    }
}
