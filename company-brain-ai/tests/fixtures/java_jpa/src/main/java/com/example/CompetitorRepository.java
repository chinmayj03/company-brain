package com.example;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface CompetitorRepository extends JpaRepository<Competitor, Long> {

    @Query("SELECT c FROM Competitor c WHERE c.lob = :lob")
    List<CompetitorDto> findByLob(String lob);
}
