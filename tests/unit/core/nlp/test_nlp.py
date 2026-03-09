import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import numpy as np
from core.nlp.models import CachedEmbedder, get_embedder, get_reranker
from core.nlp.spacy_utils import extract_spacy_metadata, is_spacy_available


@pytest.fixture
def mock_sentence_transformer():
    mock = MagicMock()

    def encode_side_effect(sentences, **kwargs):
        if isinstance(sentences, str):
            return np.array([0.1, 0.2, 0.3])
        return np.array([np.array([0.1, 0.2, 0.3]) for _ in sentences])

    mock.encode.side_effect = encode_side_effect
    mock.get_sentence_embedding_dimension.return_value = 3
    return mock


@pytest.fixture
def mock_cache():
    cache = AsyncMock()
    cache.get.return_value = None
    return cache


class TestCachedEmbedder:
    @pytest.mark.asyncio
    async def test_encode_no_cache_hit(self, mock_sentence_transformer, mock_cache):
        embedder = CachedEmbedder(mock_sentence_transformer, cache=mock_cache)

        # Test single string
        result = await embedder.encode("test sentence")

        assert mock_sentence_transformer.encode.called
        assert mock_cache.get.called
        assert mock_cache.set.called
        assert isinstance(result, np.ndarray)

    @pytest.mark.asyncio
    async def test_encode_cache_hit(self, mock_sentence_transformer):
        mock_cache_hit = AsyncMock()
        cached_val = np.array([0.9, 0.8, 0.7])
        mock_cache_hit.get.return_value = cached_val

        embedder = CachedEmbedder(mock_sentence_transformer, cache=mock_cache_hit)

        result = await embedder.encode("test sentence")

        # Model should NOT be called for encoding if cache hit
        mock_sentence_transformer.encode.assert_not_called()
        assert np.array_equal(result, cached_val)

    @pytest.mark.asyncio
    async def test_encode_batch_mixed(self, mock_sentence_transformer):
        # Scenario: 2 sentences, 1st in cache, 2nd properly missing
        mock_cache = AsyncMock()

        cached_val = np.array([0.9, 0.9, 0.9])

        async def side_effect_get(key):
            # purely dependency on hash is tricky to mock exactly without knowing hash
            # but we can return None for one and Value for another if we track calls
            # simpler: first call returns val, second call returns None
            if mock_cache.get.call_count == 1:
                return cached_val
            return None

        mock_cache.get.side_effect = side_effect_get

        embedder = CachedEmbedder(mock_sentence_transformer, cache=mock_cache)

        inputs = ["cached", "missing"]
        results = await embedder.encode(inputs)

        assert len(results) == 2
        # verify model called only for "missing"
        # The logic in CachedEmbedder groups missing texts and calls model once
        mock_sentence_transformer.encode.assert_called_once()
        args, _ = mock_sentence_transformer.encode.call_args
        assert args[0] == ["missing"]


class TestFactoryFunctions:
    def test_get_embedder(self):
        with patch("core.nlp.models.SentenceTransformer") as mock_st:
            # clear lru_cache to ensure new call
            get_embedder.cache_clear()
            embedder = get_embedder("test-model")
            assert isinstance(embedder, CachedEmbedder)
            mock_st.assert_called_with("test-model")

    def test_get_reranker(self):
        with patch("core.nlp.models.CrossEncoder") as mock_ce:
            get_reranker.cache_clear()
            _ = get_reranker("test-reranker")
            mock_ce.assert_called_with("test-reranker")


class TestSpacyUtils:
    @patch("core.nlp.spacy_utils.get_spacy_pipeline")
    def test_extract_spacy_metadata(self, mock_get_pipeline):
        # Mock nlp pipeline
        mock_nlp = MagicMock()
        mock_doc = MagicMock()

        # Setup doc structure
        mock_token = MagicMock()
        mock_token.is_space = False
        mock_doc.__iter__.return_value = [mock_token, mock_token]  # 2 tokens

        # sentences
        mock_doc.sents = [1, 2, 3]  # 3 sentences

        # entities
        ent1 = MagicMock()
        ent1.text = " Apple "
        ent1.label_ = "ORG"

        ent2 = MagicMock()
        ent2.text = "iPhone"
        ent2.label_ = "PRODUCT"

        mock_doc.ents = [ent1, ent2]

        mock_nlp.return_value = mock_doc
        mock_nlp.meta = {"name": "en_core_web_sm"}
        mock_nlp.lang = "en"

        mock_get_pipeline.return_value = mock_nlp

        metadata = extract_spacy_metadata("Apple makes iPhone")

        assert metadata["spacy_language"] == "en"
        assert metadata["spacy_model"] == "en_core_web_sm"
        assert metadata["spacy_token_count"] == "2"
        assert metadata["spacy_sentence_count"] == "3"
        assert "Apple (ORG)" in metadata["spacy_entities"]
        assert "iPhone (PRODUCT)" in metadata["spacy_entities"]

    @patch("core.nlp.spacy_utils.get_spacy_pipeline")
    def test_is_spacy_available(self, mock_pipeline):
        mock_pipeline.return_value = "something"
        assert is_spacy_available()

        mock_pipeline.return_value = None
        assert not is_spacy_available()
