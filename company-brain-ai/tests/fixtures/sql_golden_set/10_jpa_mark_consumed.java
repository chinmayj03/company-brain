package com.example.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.time.OffsetDateTime;
import java.util.List;

public interface ArtifactChangeEventRepository extends JpaRepository<ArtifactChangeEvent, Long> {

    @Modifying
    @Query("""
            UPDATE ArtifactChangeEvent e
            SET e.consumedAt = :now
            WHERE e.id IN :ids
            """)
    void markConsumed(@Param("ids") List<Long> ids, @Param("now") OffsetDateTime now);
}
