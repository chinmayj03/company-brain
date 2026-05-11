package com.example;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.cache.annotation.Cacheable;

@Service
public class CompetitorService {

    private final CompetitorRepository competitorRepository;

    public CompetitorService(CompetitorRepository competitorRepository) {
        this.competitorRepository = competitorRepository;
    }

    @Transactional(readOnly = true)
    @Cacheable("competitors")
    public List<CompetitorDto> getCompetitors(String lob) {
        return competitorRepository.findByLob(lob);
    }
}
